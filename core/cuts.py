"""
Core logic for matching headlines to transcript moments and building cutdowns.
No GUI code here — kept importable/testable on its own.
"""
import re, json, subprocess, shutil

STOPWORDS = set("""a an the and or but so to of in on for with is are was were be been
being this that these those i you he she it we they my your his her its our their
me him them us just really very like so much a lot kind sort actually literally
do does did have has had can could will would should not no yes about into
""".split())


def parse_srt(path):
    """Returns list of (idx, start_sec, end_sec, text)."""
    txt = open(path, encoding="utf-8-sig").read().replace("\r", "")
    cues = []
    for block in txt.strip().split("\n\n"):
        lines = block.split("\n")
        if len(lines) < 3:
            continue
        try:
            idx = int(lines[0])
        except ValueError:
            continue
        a, b = lines[1].split(" --> ")
        cues.append((idx, _ts(a), _ts(b), " ".join(lines[2:]).strip()))
    return cues


def _ts(s):
    h, m, r = s.split(":")
    sec, ms = r.split(",")
    return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000


def _fmt_srt_ts(s):
    ms = round(max(0, s) * 1000)
    return f"{ms//3600000:02d}:{ms//60000%60:02d}:{ms//1000%60:02d},{ms%1000:03d}"


def keywords(text):
    words = re.findall(r"[a-zA-Z']+", text.lower())
    return {w for w in words if len(w) > 2 and w not in STOPWORDS}


def score_cues(cues, headline_text):
    """Score each cue 0..1+ by keyword overlap with the headline."""
    kw = keywords(headline_text)
    if not kw:
        return [0.0] * len(cues)
    scores = []
    for _, a, b, txt in cues:
        ck = keywords(txt)
        overlap = len(kw & ck)
        scores.append(overlap / max(1, len(kw)))
    return scores


def _overlaps_used(i, cues, used):
    return any(a <= cues[i][1] < b or a < cues[i][2] <= b for a, b in used)


_TERMINATORS = (".", "?", "!", "…", '."', '?"', '!"', ".'", "?'", "!'")
_PAUSE_GAP = 0.6  # a gap this long between cues reads as a natural breath/pause


def _ends_sentence(text):
    t = text.strip()
    return t.endswith(_TERMINATORS)


def _snap_to_sentence(cues, lo, hi, used, hard_cap):
    """Pull lo/hi outward so the clip starts after a sentence end (or a pause)
    and ends at a sentence end (or a pause), instead of stopping mid-sentence
    just because that's where the caption line happened to break."""
    n = len(cues)
    while lo > 0:
        prev = cues[lo - 1]
        gap = cues[lo][1] - prev[2]
        if _ends_sentence(prev[3]) or gap > _PAUSE_GAP:
            break
        if _overlaps_used(lo - 1, cues, used):
            break
        if cues[hi][2] - prev[1] > hard_cap:
            break
        lo -= 1
    while hi < n - 1:
        cur = cues[hi]
        nxt = cues[hi + 1]
        gap = nxt[1] - cur[2]
        if _ends_sentence(cur[3]) or gap > _PAUSE_GAP:
            break
        if _overlaps_used(hi + 1, cues, used):
            break
        if nxt[2] - cues[lo][1] > hard_cap:
            break
        hi += 1
    return lo, hi


def _best_window(cues, scores, used, min_dur, max_dur):
    """Anchor on the highest-scoring free cue, then expand outward while it stays
    tight (bounded by max_dur and a density floor), so windows are punchy clips
    around the actual quote rather than always maxing out at max_dur."""
    n = len(cues)
    candidates = [i for i in range(n) if scores[i] > 0 and not _overlaps_used(i, cues, used)]
    if not candidates:
        return None
    anchor = max(candidates, key=lambda i: scores[i])
    lo = hi = anchor
    acc = scores[anchor]

    def span():
        return cues[hi][2] - cues[lo][1]

    while span() < max_dur:
        can_left = lo > 0 and not _overlaps_used(lo - 1, cues, used)
        can_right = hi < n - 1 and not _overlaps_used(hi + 1, cues, used)
        if not can_left and not can_right:
            break
        left_score = scores[lo - 1] if can_left else -1
        right_score = scores[hi + 1] if can_right else -1
        # stop growing once both neighbors are silent AND we've already met min_dur
        if left_score <= 0 and right_score <= 0 and span() >= min_dur:
            break
        if right_score >= left_score:
            hi += 1
            acc += scores[hi]
        else:
            lo -= 1
            acc += scores[lo]

    if span() < min_dur:
        # pad with neighboring (even zero-score) cues to reach a usable minimum length
        while span() < min_dur and (lo > 0 or hi < n - 1):
            if hi < n - 1 and not _overlaps_used(hi + 1, cues, used):
                hi += 1
            elif lo > 0 and not _overlaps_used(lo - 1, cues, used):
                lo -= 1
            else:
                break
    # don't let a clip start or end mid-sentence — pull the edges out to the
    # nearest sentence end or natural pause, within a generous safety cap
    hard_cap = max(max_dur * 1.35, max_dur + 15)
    lo, hi = _snap_to_sentence(cues, lo, hi, used, hard_cap)
    return (acc, lo, hi)


def pick_segments_for_headline(cues, headline_text, min_dur=18, max_dur=65, max_parts=2):
    """Return list of {start,end,text_preview} snapped to caption boundaries."""
    scores = score_cues(cues, headline_text)
    used, out = [], []
    for _ in range(max_parts):
        best = _best_window(cues, scores, used, min_dur, max_dur)
        if not best:
            break
        _, i, j = best
        s = round(max(0, cues[i][1] - 0.05), 3)
        e = round(cues[j][2] + 0.10, 3)
        used.append((s, e))
        out.append({"start": s, "end": e, "preview": f"{cues[i][3][:40]} ... {cues[j][3][-40:]}"})
    out.sort(key=lambda x: x["start"])
    return out


def pick_full_cutdown(cues, headlines, target_seconds=170, per_headline_cap=30):
    """One best window per headline, trimmed proportionally to fit target_seconds."""
    picks = []
    for h in headlines:
        segs = pick_segments_for_headline(cues, h, min_dur=10, max_dur=per_headline_cap, max_parts=1)
        if segs:
            picks.append(segs[0])
    if not picks:
        return []
    total = sum(s["end"] - s["start"] for s in picks)
    if total > target_seconds:
        scale = target_seconds / total
        for s in picks:
            mid = (s["start"] + s["end"]) / 2
            half = (s["end"] - s["start"]) * scale / 2
            s["start"], s["end"] = round(mid - half, 3), round(mid + half, 3)
    picks.sort(key=lambda x: x["start"])
    return picks


def probe_video(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate,width,height", "-of", "json", path],
        capture_output=True, text=True, check=True)
    d = json.loads(r.stdout)["streams"][0]
    num, den = d["r_frame_rate"].split("/")
    fps = round(float(num) / float(den), 3)
    return {"fps": fps if fps == int(fps) else fps, "width": d["width"], "height": d["height"]}


def build_xmeml(video_filename, fps, src_w, src_h, sequences, out_w=1080, out_h=1920):
    """sequences: list of (name, segments[{start,end}]). Returns XML string."""
    from xml.sax.saxutils import escape
    fps_i = round(fps)
    st = {"defined": False, "n": 0}
    file_def = (f'<file id="file-1"><name>{escape(video_filename)}</name>'
                f'<pathurl>file://localhost/{escape(video_filename)}</pathurl>'
                f'<rate><timebase>{fps_i}</timebase><ntsc>FALSE</ntsc></rate>'
                f'<media><video><samplecharacteristics><rate><timebase>{fps_i}</timebase>'
                f'<ntsc>FALSE</ntsc></rate><width>{src_w}</width><height>{src_h}</height>'
                f'<anamorphic>FALSE</anamorphic><pixelaspectratio>square</pixelaspectratio>'
                f'<fielddominance>none</fielddominance></samplecharacteristics></video>'
                f'<audio><samplecharacteristics><depth>16</depth><samplerate>48000</samplerate>'
                f'</samplecharacteristics><channelcount>2</channelcount></audio></media></file>')

    def fref():
        if not st["defined"]:
            st["defined"] = True
            return file_def
        return '<file id="file-1"/>'

    def seq_xml(sid, name, segs):
        v, a1, a2, pos = [], [], [], 0
        for c in segs:
            i0, i1 = round(c["start"] * fps), round(c["end"] * fps)
            d = i1 - i0
            st["n"] += 1
            k = st["n"]
            vid, x1, x2 = f"clipitem-v{k}", f"clipitem-a{k}a", f"clipitem-a{k}b"
            ci = len(v) + 1
            lk = "".join(
                f'<link><linkclipref>{x}</linkclipref><mediatype>{m}</mediatype>'
                f'<trackindex>{t}</trackindex><clipindex>{ci}</clipindex></link>'
                for x, m, t in [(vid, "video", 1), (x1, "audio", 1), (x2, "audio", 2)])
            hd = (f'<name>{escape(video_filename)}</name><enabled>TRUE</enabled>'
                  f'<rate><timebase>{fps_i}</timebase><ntsc>FALSE</ntsc></rate>'
                  f'<start>{pos}</start><end>{pos+d}</end><in>{i0}</in><out>{i1}</out>')
            v.append(f'<clipitem id="{vid}">{hd}{fref()}{lk}</clipitem>')
            a1.append(f'<clipitem id="{x1}" premiereChannelType="mono">{hd}<file id="file-1"/>'
                       f'<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack>{lk}</clipitem>')
            a2.append(f'<clipitem id="{x2}" premiereChannelType="mono">{hd}<file id="file-1"/>'
                       f'<sourcetrack><mediatype>audio</mediatype><trackindex>2</trackindex></sourcetrack>{lk}</clipitem>')
            pos += d
        return (f'<sequence id="{sid}"><name>{escape(name)}</name><duration>{pos}</duration>'
                f'<rate><timebase>{fps_i}</timebase><ntsc>FALSE</ntsc></rate>'
                f'<media><video><format><samplecharacteristics><rate><timebase>{fps_i}</timebase>'
                f'<ntsc>FALSE</ntsc></rate><width>{out_w}</width><height>{out_h}</height>'
                f'<anamorphic>FALSE</anamorphic><pixelaspectratio>square</pixelaspectratio>'
                f'<fielddominance>none</fielddominance></samplecharacteristics></format>'
                f'<track>{"".join(v)}</track></video>'
                f'<audio><numOutputChannels>2</numOutputChannels><format><samplecharacteristics>'
                f'<depth>16</depth><samplerate>48000</samplerate></samplecharacteristics></format>'
                f'<track>{"".join(a1)}</track><track>{"".join(a2)}</track></audio></media></sequence>')

    body = "\n".join(seq_xml(f"seq-{i+1}", name, segs) for i, (name, segs) in enumerate(sequences))
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n'
            f'<xmeml version="4"><project><name>ThumbCutter Export</name>'
            f'<children>\n{body}\n</children></project></xmeml>')


def build_srt(cues, segments):
    out, pos, n = [], 0, 0
    for sg in segments:
        for _, a, b, tx in cues:
            if b <= sg["start"] + 0.05 or a >= sg["end"]:
                continue
            a2, b2 = max(a, sg["start"]), min(b, sg["end"])
            n += 1
            out.append(f"{n}\n{_fmt_srt_ts(pos+a2-sg['start'])} --> {_fmt_srt_ts(pos+b2-sg['start'])}\n{tx.strip()}\n")
        pos += sg["end"] - sg["start"]
    return "\n".join(out)

"""Save a javascript_tool capture (innerText repeated, persisted to a file) as a supplemental doc."""
import json, sys, pathlib
src, out, url, note = sys.argv[1:5]
data = json.load(open(src))
txt = data[0]["text"] if isinstance(data, list) else data
txt = txt.split("\n\n(captured at origin")[0].strip()
txt = json.loads(txt)              # JS results come back JSON-encoded
first = txt.split("<<<SPLIT>>>")[0]
lines = [l.strip() for l in first.split("\n") if l.strip()]
hdr = f"SOURCE: {url}\nRETRIEVED: 2026-10-03 23:20 UTC\nCAPTURE: manual, built-in browser innerText of <main>; blank lines removed (supplemental; {note})\n\n"
pathlib.Path(out).write_text(hdr + "\n".join(lines) + "\n")
print(out, len(lines), "lines", sum(map(len, lines)), "chars |", lines[0][:80])

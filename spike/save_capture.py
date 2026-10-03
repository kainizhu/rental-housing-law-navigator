"""Save a built-in-browser get_page_text tool result (persisted JSON) as a supplemental corpus file, verbatim
except: whitespace-only lines dropped and trailing spaces stripped."""
import json, re, sys, pathlib
src, out, url, note = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
data = json.load(open(src))
txt = next(b["text"] for b in data if b["text"].startswith("[get_page_text]"))
body = txt.split("---\n", 1)[1]
body = re.split(r"\n\nTab Context:", body)[0]
lines = [l.rstrip() for l in body.split("\n")]
lines = [l.strip() for l in lines if l.strip()]
hdr = f"SOURCE: {url}\nRETRIEVED: 2026-10-03 23:15 UTC\nCAPTURE: manual, built-in browser page text; blank lines removed (supplemental; {note})\n\n"
pathlib.Path(out).write_text(hdr + "\n".join(lines) + "\n")
print(out, len(lines), "lines", sum(len(l) for l in lines), "chars")

#!/usr/bin/env python3
import re
import html
from pathlib import Path

# This script generates transcript_colored.html from transcript.txt

IN_PATH = Path("transcript.txt")
OUT_PATH = Path("transcript_colored.html")

PLAYER_COLORS = {
    "Reimu": "#ff5c5c",
    "Marisa": "#ffd166",
    "Rumia": "#c77dff",
    "Daiyousei": "#2ee6d6",
    "Cirno": "#57a5ff",
    "Koakuma": "#ff9f43",
    "Meiling": "#4cff7a",
    "Patchouli": "#b07dff",
    "Sakuya": "#c7cedb",
    "Remilia": "#ff4d6d",
    "Flandre": "#ff7a00",
}

ROLE_STYLES = {
    "mafia": {"label": "MAFIA", "bg": "#500F0F", "fg": "#e5e7eb"},
    "town": {"label": "TOWN", "bg": "#41c54a", "fg": "#000000"},
    "doctor": {"label": "DOCTOR", "bg": "#f2ff00", "fg": "#041b0b"},
    "sheriff": {"label": "SHERIFF", "bg": "#2563eb", "fg": "#071427"},
    "vigilante": {"label": "VIGILANTE", "bg": "#00d0ff", "fg": "#1a0a00"},
}

def parse_roles(lines):
    roles = {}
    in_players = False
    for line in lines:
        s = line.strip()
        if s == "THE PLAYERS:":
            in_players = True
            continue
        if in_players:
            if not s:
                break
            m = re.match(r"^([A-Za-z0-9_]+)\s*:\s*(.+?)\s*$", s)
            if m:
                roles[m.group(1)] = m.group(2).strip().lower()
    return roles

def make_badge(role):
    r = ROLE_STYLES.get(role)
    if not r:
        return ""
    return (
        f'<span class="badge" style="background:{r["bg"]};color:{r["fg"]}">'
        f'{html.escape(r["label"])}</span>'
    )

def main():
    raw = IN_PATH.read_text(encoding="utf-8", errors="replace")
    lines = raw.splitlines()
    roles = parse_roles(lines)

    bracket_speaker = re.compile(r"^\[([^\]]+)\]\s*([A-Za-z0-9_]+)\s*:\s*(.*)$")
    plain_speaker = re.compile(r"^([A-Za-z0-9_]+)\s*:\s*(.*)$")

    out = []
    out.append("""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mafia Transcript (Colored)</title>
<style>
  :root { color-scheme: dark; }
  body {
    font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
    margin: 24px; line-height: 1.35;
    background: #0b0f19; color: #e5e7eb;
  }
  .wrap { max-width: 1050px; margin: 0 auto; }
  .header { display:flex; align-items:baseline; justify-content:space-between; gap:16px; }
  h1 { font-size: 20px; margin: 0 0 10px 0; color: #f3f4f6; }
  .legend { display:flex; flex-wrap:wrap; gap:8px; font-size: 12px; }
  .pill {
    border-radius: 999px; padding: 3px 9px;
    border: 1px solid rgba(255,255,255,0.10);
    background: rgba(255,255,255,0.04);
  }
  .log {
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 12px;
    padding: 12px 14px;
    background: rgba(255,255,255,0.03);
    box-shadow: 0 8px 30px rgba(0,0,0,0.35);
    backdrop-filter: blur(6px);
  }
  .line { padding: 5px 0; border-bottom: 1px dashed rgba(255,255,255,0.07); }
  .line:last-child { border-bottom: none; }
  .meta { color: rgba(229,231,235,0.60); font-style: italic; }
  .channel { color: rgba(229,231,235,0.75); font-weight: 600; }
  .speaker { font-weight: 750; }
  .badge { font-size: 11px; padding: 2px 6px; border-radius: 6px; margin-left: 8px; vertical-align: 1px; }
  .text { white-space: pre-wrap; }
  .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
  ::selection { background: rgba(99,102,241,0.35); }
  a { color: #93c5fd; }
</style>
</head>
<body>
<div class="wrap">
  <div class="header">
    <h1>Mafia Transcript (Colored)</h1>
    <div class="legend">
""")

    for name, color in PLAYER_COLORS.items():
        role = roles.get(name, "")
        badge = ROLE_STYLES.get(role, {}).get("label", role.upper() if role else "")
        out.append(
            f'<span class="pill"><span class="speaker" style="color:{color}">{html.escape(name)}</span>'
            + (f' <span class="mono" style="color:rgba(229,231,235,0.55)">({html.escape(badge)})</span>' if badge else "")
            + "</span>"
        )

    out.append("""
    </div>
  </div>
  <div class="log">
""")

    for line in lines:
        s = line.rstrip("\n")
        esc = html.escape(s)

        if not s.strip():
            out.append('<div class="line meta">&nbsp;</div>')
            continue

        is_meta = bool(re.match(r"^(Night|Day)\s+\d+\b", s)) or \
                  "has been found dead" in s or \
                  s.startswith("Remaining players") or \
                  s.startswith("Game started") or \
                  s.startswith("Roles have been distributed") or \
                  s.startswith("-----") or \
                  s.endswith("phase begins.") or \
                  s.endswith("phase ends.") or \
                  s.startswith("ROLE REVEAL") or \
                  s.startswith("Postgame") or \
                  s.startswith("MVP voting")

        m = bracket_speaker.match(s)
        if m:
            channel, name, text = m.group(1), m.group(2), m.group(3)
            color = PLAYER_COLORS.get(name, "#e5e7eb")
            role = roles.get(name, "")
            out.append(
                '<div class="line">'
                f'<span class="channel">[{html.escape(channel)}]</span> '
                f'<span class="speaker" style="color:{color}">{html.escape(name)}</span>'
                f'{make_badge(role)}: '
                f'<span class="text">{html.escape(text)}</span>'
                "</div>"
            )
            continue

        m = plain_speaker.match(s)
        if m:
            name, text = m.group(1), m.group(2)
            color = PLAYER_COLORS.get(name, "#e5e7eb")
            role = roles.get(name, "")
            out.append(
                '<div class="line">'
                f'<span class="speaker" style="color:{color}">{html.escape(name)}</span>'
                f'{make_badge(role)}: '
                f'<span class="text">{html.escape(text)}</span>'
                "</div>"
            )
            continue

        out.append(f'<div class="line {"meta" if is_meta else ""}">{esc}</div>')

    out.append("""
  </div>
</div>
</body>
</html>
""")

    OUT_PATH.write_text("\n".join(out), encoding="utf-8")
    print(f"Wrote: {OUT_PATH.resolve()}")

if __name__ == "__main__":
    main()

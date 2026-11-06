"""Rebuilds demo/app.html from the app's index.html.  Run it after the app's UI changes:
    python make_demo.py path/to/index.html
"""
import sys
src = open(sys.argv[1], encoding="utf-8").read()
out = src.replace("<head>", '''<head>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'unsafe-inline'; img-src https: data: blob:; media-src https: blob:; connect-src https://any-presence.kagitreble.workers.dev https://itunes.apple.com https://api.vndb.org; font-src data:; base-uri 'none'; object-src 'none'; form-action 'none'">
<script src="demo.js"></script>''', 1)
assert out != src, "no <head> found"
open("demo/app.html", "w", encoding="utf-8").write(out)
print("demo/app.html updated")

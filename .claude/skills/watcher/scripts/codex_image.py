"""Runs on the VM (piped over ssh). argv: <base64 of the prompt> [--ref <image_path_on_vm>]. The prompt goes over
base64 because run.sh joins argv into a remote shell line, so quotes and apostrophes would break it. Asks the VM's Codex (logged in with
ChatGPT, image_generation on) to generate one image for the prompt, then prints one JSON line:
{"ok": true, "path": <png on the VM>, "b64": <base64 of it>} or {"ok": false, "error": ...}.

Pull it into the scratchpad with:
  ./run.sh codex_image.py $(printf %s "prompt" | base64 -w0) | python3 -c "import sys,json,base64;d=json.loads(sys.stdin.read().strip().splitlines()[-1]);open('out.png','wb').write(base64.b64decode(d['b64']));print(d['path'])"
Takes about a minute. Codex writes into ~/.codex/generated_images/<session>/, not where it's told."""
import base64, glob, json, os, re, subprocess, sys

args = sys.argv[1:]
ref = None
if "--ref" in args:
    i = args.index("--ref")
    ref = args[i + 1]
    del args[i:i + 2]
prompt = base64.b64decode(args[0]).decode().strip() if args else ""
if not prompt:
    print(json.dumps({"ok": False, "error": "no prompt"}))
    sys.exit(1)

cmd = ["codex", "exec", "--skip-git-repo-check", "-s", "read-only", "-C", "/tmp"]
if ref:
    cmd += ["-i", ref]
cmd.append("--")  # -i takes several values, so without this it swallows the prompt as another image path
cmd.append("Use the imagegen skill to generate exactly one image, then stop. Don't run shell commands. Image: " + prompt)
try:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
except subprocess.TimeoutExpired:
    print(json.dumps({"ok": False, "error": "codex timed out after 300s"}))
    sys.exit(1)

out = r.stdout + r.stderr
m = re.search(r"session id: (\S+)", out)
files = sorted(glob.glob(os.path.expanduser(f"~/.codex/generated_images/{m.group(1)}/*.png")) if m else [],
               key=os.path.getmtime)
if not files:
    print(json.dumps({"ok": False, "error": "no image produced", "tail": out[-800:]}))
    sys.exit(1)
path = files[-1]
print(json.dumps({"ok": True, "path": path, "b64": base64.b64encode(open(path, "rb").read()).decode()}))

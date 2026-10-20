#!/usr/bin/env python3
"""
Fully automatic episode maker (runs in GitHub Actions, free).
Picks the next episode not listed in done.txt, then:
  free AI images (Pollinations) -> free AI voice (edge-tts) -> ffmpeg -> out/<episode>.mp4 + out/meta.json
Set env OFFLINE=1 to test with plain colour images and silent audio.
"""
import asyncio, json, os, subprocess, sys, time, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
OUT, TMP = os.path.join(HERE, "out"), os.path.join(HERE, "tmp")
os.makedirs(OUT, exist_ok=True); os.makedirs(TMP, exist_ok=True)
OFFLINE = os.environ.get("OFFLINE") == "1"
VOICE, RATE = "en-US-AnaNeural", "-5%"
W, H, FPS, PAD = 1080, 1920, 30, 0.6


def run(cmd):
    r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if r.returncode:
        sys.exit(f"command failed: {' '.join(cmd[:3])}...\n{r.stderr[-800:]}")


def duration(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", p], capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def full_prompt(data, shot):
    text = shot["image_prompt"]
    chars = [f"{n}: {d}" for n, d in data["characters"].items() if n in text]
    return f"{text}. Characters: {'; '.join(chars)}. {data['style']}"


def gen_image(prompt, path, seed):
    if OFFLINE:
        return run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"color=c=0x{(seed*977)%0xFFFFFF:06x}:s=1024x1024", "-frames:v", "1", path])
    import requests
    url = ("https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt)
           + f"?width={W}&height={H}&nologo=true&seed={seed}")
    for attempt in range(5):
        try:
            r = requests.get(url, timeout=180)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
                open(path, "wb").write(r.content)
                return
        except Exception as e:
            print("image retry:", e)
        time.sleep(10 * (attempt + 1))
    sys.exit("image generation failed after retries")


async def tts(text, path):
    import edge_tts
    await edge_tts.Communicate(text, VOICE, rate=RATE).save(path)


def make_clip(img, audio, out, idx):
    dur = duration(audio) + PAD
    frames = int(dur * FPS)
    z = "min(zoom+0.0007,1.15)" if idx % 2 == 0 else "if(eq(on,0),1.15,max(zoom-0.0007,1.0))"
    vf = (f"scale={W*2}:{H*2}:force_original_aspect_ratio=increase,crop={W*2}:{H*2},"
          f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS},format=yuv420p")
    run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", img, "-i", audio, "-vf", vf,
         "-af", f"apad=pad_dur={PAD}", "-t", f"{dur:.2f}", "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "20", "-c:a", "aac", "-b:a", "160k", "-shortest", out])


def main():
    data = json.load(open(os.path.join(HERE, "episodes.json")))
    done_path = os.path.join(HERE, "done.txt")
    done = set(open(done_path).read().split()) if os.path.exists(done_path) else set()
    todo = [e for e in data["episodes"] if e["id"] not in done]
    if not todo:
        sys.exit("All episodes in episodes.json are done. Add more episodes.")
    ep = todo[0]
    print("Making", ep["id"], ep["title"])
    clips = []
    for i, shot in enumerate(ep["shots"], 1):
        name = f"{ep['id']}_s{i}"
        img = os.path.join(TMP, name + ".png")
        gen_image(full_prompt(data, shot), img, seed=1000 + int(ep["id"][2:]) * 10 + i)
        audio = os.path.join(TMP, name + ".mp3")
        if OFFLINE:
            run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", "4", audio])
        else:
            asyncio.run(tts(shot["narration"], audio))
        clip = os.path.join(TMP, name + ".mp4")
        make_clip(img, audio, clip, i)
        clips.append(clip)
    lst = os.path.join(TMP, "list.txt")
    open(lst, "w").write("".join(f"file '{c}'\n" for c in clips))
    video = os.path.join(OUT, f"{ep['id']}.mp4")
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", video])
    story = " ".join(s["narration"] for s in ep["shots"])
    meta = {"id": ep["id"], "video": video,
            "title": f"{ep['title']} | {data['channel']} #Shorts",
            "description": f"{story}\n\nMoral: {ep['moral']}.\nA new story every day! Subscribe for more cozy tales.\nThis video uses AI-generated visuals and voice.",
            "tags": ["kids stories", "bedtime story", "moral story", "cartoon for kids", "bear and frog"]}
    json.dump(meta, open(os.path.join(OUT, "meta.json"), "w"), indent=2)
    open(done_path, "a").write(ep["id"] + "\n")
    print("Done:", video)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Episode maker v2 (runs in GitHub Actions, free).
Picks the next episode not in done.txt, then:
  free AI images (Pollinations) + free AI voice (edge-tts)
  -> multi-cut camera moves, big captions, original music-box lullaby, end card
  -> out/<episode>.mp4 + out/meta.json
Env OFFLINE=1 = test mode (no internet). IMG_DIR=<folder with s1.png s2.png s3.png> uses those images in test mode.
"""
import asyncio, json, math, os, random, shutil, struct, subprocess, sys, time, urllib.parse, wave

HERE = os.path.dirname(os.path.abspath(__file__))
OUT, TMP = os.path.join(HERE, "out"), os.path.join(HERE, "tmp")
os.makedirs(OUT, exist_ok=True); os.makedirs(TMP, exist_ok=True)
OFFLINE = os.environ.get("OFFLINE") == "1"
IMG_DIR = os.environ.get("IMG_DIR")
VOICE, RATE = "en-US-AnaNeural", "-5%"
W, H, FPS = 1080, 1920, 30
PAD, END_PAD = 0.5, 2.6          # pause after each line; extra tail on last line for the end card
SCENE_SECONDS = 2.4              # target length of each camera move
BRAND = "SMW BOBO & FERN TALES"
FONT_CANDIDATES = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                   "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"]
FONT = next((f for f in FONT_CANDIDATES if os.path.exists(f)), None)

# (zoom start, zoom end, x0, y0, x1, y1)  x/y are 0..1 positions of the crop window
PATTERNS = [
    (1.00, 1.20, 0.50, 0.50, 0.50, 0.40),   # slow push in
    (1.28, 1.28, 0.15, 0.45, 0.85, 0.45),   # pan left to right
    (1.35, 1.10, 0.50, 0.30, 0.50, 0.50),   # pull out from the top (faces)
    (1.12, 1.32, 0.50, 0.65, 0.50, 0.50),   # push toward the lower part
    (1.28, 1.28, 0.85, 0.50, 0.15, 0.50),   # pan right to left
    (1.05, 1.30, 0.30, 0.40, 0.60, 0.40),   # drift and push
]


def run(cmd):
    r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if r.returncode:
        sys.exit(f"command failed: {' '.join(cmd[:4])}...\n{r.stderr[-1200:]}")


def duration(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", p], capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def full_prompt(data, shot):
    text = shot["image_prompt"]
    chars = [f"{n}: {d}" for n, d in data["characters"].items() if n in text]
    return (f"{data['style']}. Same consistent character design in every image. "
            f"Characters: {'; '.join(chars)}. Scene: {text}. No text, no letters, no watermark.")


def gen_image(prompt, path, seed, shot_no):
    if OFFLINE:
        if IMG_DIR:
            shutil.copy(os.path.join(IMG_DIR, f"s{shot_no}.png"), path)
            return
        return run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"color=c=0x{(seed*977)%0xFFFFFF:06x}:s=1024x1024", "-frames:v", "1", path])
    import requests
    url = ("https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt)
           + f"?width={W}&height={H}&nologo=true&model=flux&seed={seed}")
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


def make_scene(img, frames, pat, out):
    zs, ze, x0, y0, x1, y1 = pat
    N = max(frames, 1)
    # crop 7% off the bottom first: hides any free-service logo in the corner
    vf = (f"crop=iw:ih*0.93:0:0,scale={W*2}:{H*2}:force_original_aspect_ratio=increase,crop={W*2}:{H*2},"
          f"zoompan=z='{zs}+({ze}-{zs})*on/{N}'"
          f":x='(iw-iw/zoom)*({x0}+({x1}-{x0})*on/{N})'"
          f":y='(ih-ih/zoom)*({y0}+({y1}-{y0})*on/{N})'"
          f":d={N}:s={W}x{H}:fps={FPS},format=yuv420p")
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", img, "-vf", vf, "-frames:v", str(N), "-an",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-r", str(FPS), out])


def make_motion_scene(clip_path, frames, out):
    """Use a real AI-video clip: crop off the bottom (hides corner watermarks), fill 9:16, loop/trim to length."""
    vf = (f"crop=iw:ih*0.93:0:0,scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
          f"fps={FPS},format=yuv420p")
    run(["ffmpeg", "-y", "-loglevel", "error", "-stream_loop", "-1", "-i", clip_path, "-vf", vf,
         "-frames:v", str(frames), "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
         "-r", str(FPS), out])


def make_music(path, seconds, seed):
    """Original music-box lullaby (generated, so no copyright issues)."""
    sr = 24000
    rnd = random.Random(seed)
    scale = [261.63, 293.66, 329.63, 392.00, 440.00, 523.25]   # C major pentatonic
    n = int(seconds * sr)
    buf = [0.0] * n
    beat = 0.5
    idx = 2
    t = 0.0
    while t < seconds:
        idx = max(0, min(len(scale) - 1, idx + rnd.choice([-2, -1, -1, 0, 1, 1, 2])))
        f = scale[idx]
        start = int(t * sr)
        for k in range(min(int(1.6 * sr), n - start)):
            tt = k / sr
            env = math.exp(-3.2 * tt)
            buf[start + k] += env * (math.sin(2 * math.pi * f * tt) + 0.35 * math.sin(2 * math.pi * 2 * f * tt))
        if int(t / beat) % 4 == 0:   # soft bass every 2 seconds
            for k in range(min(int(1.8 * sr), n - start)):
                tt = k / sr
                buf[start + k] += 0.7 * math.exp(-1.8 * tt) * math.sin(2 * math.pi * (f / 2) * tt)
        t += beat
    peak = max(1e-6, max(abs(x) for x in buf))
    with wave.open(path, "w") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(b"".join(struct.pack("<h", int(x / peak * 22000)) for x in buf))


def caption_chunks(text, max_chars=15):
    """Short caption chunks (about 2 words) so they always fit the screen width."""
    chunks, cur = [], ""
    for w in text.replace("\n", " ").split():
        if cur and len(cur) + 1 + len(w) > max_chars:
            chunks.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        chunks.append(cur)
    return chunks


def drawtext(textfile, start, end, size, y, color="white", border=9):
    ff = f"fontfile={FONT}:" if FONT else ""
    return (f"drawtext={ff}textfile={textfile}:expansion=none:fontsize={size}:fontcolor={color}"
            f":borderw={border}:bordercolor=black:x=(w-text_w)/2:y={y}"
            f":enable='between(t,{start:.3f},{end:.3f})'")


def main():
    data = json.load(open(os.path.join(HERE, "episodes.json")))
    done_path = os.path.join(HERE, "done.txt")
    done = set(open(done_path).read().split()) if os.path.exists(done_path) else set()
    todo = [e for e in data["episodes"] if e["id"] not in done]
    if not todo:
        sys.exit("All episodes in episodes.json are done. Add more episodes.")
    ep = todo[0]
    print("Making", ep["id"], ep["title"])
    epn = int(ep["id"][2:])
    scene_clips, voice_wavs, captions = [], [], []
    clock, pat_i = 0.0, epn
    nshots = len(ep["shots"])
    for i, shot in enumerate(ep["shots"], 1):
        img = os.path.join(TMP, f"s{i}.png")
        motion = os.path.join(HERE, "clips", f"{ep['id']}_s{i}.mp4")   # optional real AI-video clip
        use_motion = os.path.exists(motion)
        if use_motion:
            print(f"  using motion clip for shot {i}: {motion}")
        else:
            gen_image(full_prompt(data, shot), img, 1000 + epn * 10 + i, i)
        mp3 = os.path.join(TMP, f"v{i}.mp3")
        if OFFLINE:
            secs = max(3.0, 0.38 * len(shot["narration"].split()))
            run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                 "-t", f"{secs:.2f}", mp3])
        else:
            asyncio.run(tts(shot["narration"], mp3))
        voice_len = duration(mp3)
        pad = END_PAD if i == nshots else PAD
        frames = int(round((voice_len + pad) * FPS))
        shot_secs = frames / FPS
        # voice track for this shot, padded to the exact shot length
        wav = os.path.join(TMP, f"v{i}.wav")
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp3, "-af", "apad", "-t", f"{shot_secs:.4f}",
             "-ar", "24000", "-ac", "1", wav])
        voice_wavs.append(wav)
        # camera moves: several cuts per image
        if use_motion:
            clip = os.path.join(TMP, f"c{i}_0.mp4")
            make_motion_scene(motion, frames, clip)
            scene_clips.append(clip)
            n = 0
        else:
            n = max(2, int(round(shot_secs / SCENE_SECONDS)))
        base, extra = divmod(frames, max(n, 1))
        for j in range(n):
            fr = base + (1 if j < extra else 0)
            clip = os.path.join(TMP, f"c{i}_{j}.mp4")
            make_scene(img, fr, PATTERNS[pat_i % len(PATTERNS)], clip)
            pat_i += 1
            scene_clips.append(clip)
        # captions timed across the spoken part of this shot
        chunks = caption_chunks(shot["narration"])
        weights = [max(1, len(c)) for c in chunks]
        tot = sum(weights)
        t = clock + 0.1
        for c, wgt in zip(chunks, weights):
            d = (voice_len - 0.1) * wgt / tot
            captions.append((c.upper(), t, t + d))
            t += d
        clock += shot_secs
    total = clock

    # video: join all camera-move clips
    lst = os.path.join(TMP, "list.txt")
    open(lst, "w").write("".join(f"file '{c}'\n" for c in scene_clips))
    raw = os.path.join(TMP, "raw.mp4")
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", raw])

    # audio: voice + quiet music
    vl = os.path.join(TMP, "vlist.txt")
    open(vl, "w").write("".join(f"file '{w}'\n" for w in voice_wavs))
    voice = os.path.join(TMP, "voice.wav")
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", vl, "-c", "copy", voice])
    music = os.path.join(TMP, "music.wav")
    make_music(music, total + 1, epn)
    mix = os.path.join(TMP, "mix.m4a")
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", voice, "-i", music, "-filter_complex",
         f"[0:a]volume=1.0[v];[1:a]volume=0.10,afade=t=out:st={max(0,total-2.2):.2f}:d=2.2[m];"
         "[v][m]amix=inputs=2:duration=first:dropout_transition=0,volume=2,alimiter=limit=0.95",
         "-t", f"{total:.3f}", "-c:a", "aac", "-b:a", "160k", mix])

    # captions + brand + end card, burned in one final pass
    filters = []
    for k, (text, a, b) in enumerate(captions):
        tf = os.path.join(TMP, f"cap{k}.txt")
        open(tf, "w").write(text)
        filters.append(drawtext(tf, a, b, 84, "h*0.70"))
    bf = os.path.join(TMP, "brand.txt"); open(bf, "w").write(BRAND)
    filters.append(drawtext(bf, 0, total, 46, "h*0.05", border=5))
    ef = os.path.join(TMP, "end.txt"); open(ef, "w").write("SUBSCRIBE FOR MORE STORIES!")
    filters.append(drawtext(ef, total - END_PAD + 0.2, total, 78, "h*0.42", color="yellow"))
    video = os.path.join(OUT, f"{ep['id']}.mp4")
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", raw, "-i", mix, "-vf", ",".join(filters),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p",
         "-c:a", "copy", "-shortest", "-movflags", "+faststart", video])

    story = " ".join(s["narration"] for s in ep["shots"])
    meta = {"id": ep["id"], "video": video,
            "title": f"{ep['title']} | {data['channel']} #Shorts",
            "description": f"{story}\n\nMoral: {ep['moral']}.\nA new story every day! Subscribe for more cozy tales.\nThis video uses AI-generated visuals and voice.",
            "tags": ["kids stories", "bedtime story", "moral story", "cartoon for kids", "bear and frog"]}
    json.dump(meta, open(os.path.join(OUT, "meta.json"), "w"), indent=2)
    open(done_path, "a").write(ep["id"] + "\n")
    print(f"Done: {video} ({total:.1f}s, {len(scene_clips)} camera moves)")


if __name__ == "__main__":
    main()

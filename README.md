# Boxy Control

Control stack for **Boxy**, a Unitree Go2.

```
backend/                 FastAPI: robot WebRTC link, teleop, video, speech, VLA
unitree_webrtc_connect/  Go2 WebRTC driver (imported by the backend)
frontend/                Next.js 16 + Tailwind 4 + shadcn/ui + Vercel AI SDK, linted by Biome
.env                     OPENAI_API_KEY, BACKEND_URL (frontend/.env.local symlinks here)
```

## Run

```bash
./run.sh            # backend :8000 + frontend :3000
./run.sh backend    # FastAPI only; connects to the robot (LocalAP) on startup
./run.sh frontend   # Next.js only
```

Set the robot IP in `backend/config.py`.

## How the pieces talk

- The browser calls FastAPI through `/backend/*` (a rewrite in `frontend/next.config.ts`), so there is one origin and no CORS.
- `/api/chat` is a Next.js route using the AI SDK (`streamText` + tools). Its tools in
  `frontend/src/lib/robot-tools.ts` call FastAPI directly: `getStatus`, `look` (camera snapshot),
  `move` (F/B/L/R tokens), `navigate` (VLA loop), `speak`, `stop`.
  `move` and `navigate` need an Approve click in the chat before the robot moves.

## 3D avatar

The left sidebar shows the official Unitree Go2 model (three.js + `urdf-loader`). The model
files are not in git; download them once (about 25 MB, BSD-3, from
`unitreerobotics/unitree_ros`):

```
./scripts/fetch_go2_model.sh      # -> frontend/public/go2/
```

When the robot is connected, the avatar's legs follow the 12 live motor angles and its body
follows odometry (`GET /robot/state3d`). Offline, it stands and breathes. Expand it to full
screen with the ⤢ button.

## Semantic objects in 3D

With the optional vision stack installed, the backend runs YOLOE (prompt-free, about 4,500
classes) on the camera about 4 times a second (about 30 ms per frame on an Apple GPU). It
places each detected person or object in the 3D view:

- **Direction:** the box's position in the image (front camera, pinhole model).
- **Distance:** the lidar voxels inside the box. Without lidar, the box's bottom edge on a
  flat floor.
- **Tracking:** objects are tracked in world coordinates with stable IDs and dropped
  after 2 s unseen.

```
uv pip install --python .venv/bin/python -r requirements-vision.txt
./scripts/fetch_yolo_weights.sh
```

`GET /world/objects` lists them, and `POST /world/toggle` pauses the loop.

Objects are drawn with real glTF models where one fits the label: person, chair, sofa,
plant, vase, cup, bottle, traffic cone, and dog/cat. Anything else is a labelled block.
Each model is sized from the detection: its measured height (or width, if the box was cut
off), held to a realistic range for that kind of object. People and pets walk while they
move. Download the models once (about 30 MB, Khronos glTF samples, CC0/CC-BY; credits in
`frontend/public/models/CREDITS.md`):

```
./scripts/fetch_object_models.sh
```

The lidar map leaves out the floor: its height is measured from the lowest voxels, and
anything within 8 cm of it is skipped. The 3D view's **Objects** and **Voxels**
toggles show or hide each layer. Camera numbers are estimates: set `CAMERA_HFOV_DEG` and
`CAMERA_PITCH_DEG` in `.env` once calibrated.

## Voice: "Hey Boxy"

Open the **Voice** card on the dashboard:

- **Hold to talk** (or hold Space while the button is focused): the dog says
  "I'm listening!", then records its own mic for as long as you hold. When you let go it
  says "Thanks!", transcribes what you said, asks the chat LLM, says the answer out loud,
  and carries out any move or task. Saying "stop" halts it at once. Recording ends on its
  own after 30 s.
- **Start listening** turns on "Hey Boxy" listening through the dog's own microphone.
  The bar shows the mic level live, and the card lists what Boxy heard and said back.
- **Hear the mic (5s)** records five seconds from the dog's microphone and plays it in
  the browser, so you can check what it actually hears.

To listen from startup without pressing the button, set `VOICE_AUTO_LISTEN=1` in `.env`.

- **"Hey Boxy, stop"** (also halt, freeze, stay, cancel) stops all motion at once,
  without waiting on a model.
- **"Hey Boxy, \<anything\>"** goes through the same chat brain as the chat page.
  Boxy says the reply out loud and acts on it: a simple move is driven directly, and a
  goal like "go to the chair" starts a System 2 / System 1 run.
- **A bare "Hey Boxy"** gets "Woof? I'm listening!", and the next sentence within
  10 s doesn't need the wake word.

The mic is muted only while Boxy is talking, so it can hear "stop" while it moves.
`GET /voice/status` shows the listener state and the last 20 commands with Boxy's replies.

## System 2 (chat) / System 1 (VLA)

- **System 2 is the chat model:** the dashboard chat (`frontend/src/app/api/chat/route.ts`)
  and the voice chat (`backend/services/chat/`). It talks with you and decides when the
  robot should move. **Everything it does is a tool call**: every step must call a tool,
  and its turn ends when it answers.
  - `reply(text)`: a message in the chat window. Ends a dashboard chat turn.
  - `speak(text)`: Boxy says it out loud. Ends a voice turn. In the dashboard chat, the
    **Voice replies** switch decides whether Boxy also speaks each reply.
  - `navigate(instruction, done_when)`: anything that needs the camera ("go to the chair").
  - `move(tokens)`: short moves that don't need the camera ("back up").
  - `stop()`, plus `look` and `getStatus` in the dashboard chat.
- **System 1 is the VLA loop** (`backend/services/vla/`). For one `navigate` instruction it
  repeats: camera frame → GPT (`system1.py`) → `done`, or up to two small F/B/L/R moves →
  drive them → next frame. It stops when it reports `done`, when it hits a limit
  (40 steps / 180 s / 4 frames in a row holding still), or when you stop it. A lidar check
  blocks forward moves when something is within 0.45 m.
- **The outcome goes back to System 2.** In the dashboard chat, `navigate` waits until
  System 1 finishes and returns the outcome to the model. For voice, System 1 runs in the
  background and its result comes back to System 2 as a "[System 1 report]" message. In
  both cases System 2 tells you how it went, and for a multi-part request it sends the
  next instruction.

Set System 1's model in `.env` (`VLA_SYSTEM1_MODEL=gpt-5-mini`, `VLA_SYSTEM1_EFFORT=minimal`)
and the dashboard chat's model with `CHAT_MODEL`. To test System 1 alone, without the chat:
`python scripts/vla_prompt.py "walk to the chair" --done-when "the chair fills the frame"`.

## Frontend scripts

```bash
cd frontend
npm run dev      # dev server
npm run lint     # biome check
npm run format   # biome format --write
npx shadcn@latest add <component>
```

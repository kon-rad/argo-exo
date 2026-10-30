---
title: Meet the Exo Argo Companion, a Ghost for Your Keys and Your Desk
slug: argo-companion
date: 2026-10-20
order: 9
summary: A small ghost-shaped AI companion that stands on your desk or hangs from your keys. Hold a button, ask about your own notes, and your agent answers out loud. What it is, how a day with it works, what's inside, and what is still only a render.
---

We already write everything down. Notes, plans, journal entries, research: it all goes into a second brain, and a team of AI agents works on it all day. The problem is getting at it. To ask your own notes a question you open a laptop, find the app, type, and wait. Most of the time you don't bother, and the thing you wrote last week stays where you wrote it.

The Exo Argo Companion is our answer to that. It's a small ghost, about the size of a big keychain fob, that stands on your desk or hangs from your keys. Pick it up and it wakes. Hold the button on its right side, ask a question, let go, and it answers out loud, from your own notes. Its name is Ghost. It has its own personality and an agent inside with its own memory, so it remembers what you told it yesterday, and it works in your second brain the way our other agents do: it searches it, reads it and files notes in it. Between questions it lives on its little screen like a pet.

**Nothing in this post is built yet.** The pictures are concept renders made with an image model. The parts are on our bench from the Exo brooch, the plan is written, and the first breadboard build starts this week. We're showing it now because we want to hear whether you'd use one.

<figure class="post-fig wide"><img src="/static/img/blog/argo-companion/01-desk-hero.webp" alt="A small frosted clear ghost-shaped device standing on a wooden desk next to a keyboard and coffee cup. A small screen in its head shows a pixel ghost face, two pink lights glow either side of a tiny camera lens, a gold button sits below, and a round speaker grille sits at the bottom between three rounded feet. A keyring loop is at the top." width="1200" height="896" loading="lazy"><figcaption><span class="fig">Fig. 1.</span> Concept render: the companion on a desk. Not a photograph. The build drops the front button; its two keys are on the sides.</figcaption></figure>

## What it does

One job: **you ask, your agent answers, out loud, from your own second brain.**

- **Ask about your notes.** "What did I decide about the battery?" It searches your second brain and reads you the answer.
- **Talk to any of your agents.** "Let me talk to the researcher." The voice and the face change to that agent.
- **Save a thought.** "Remember that the smaller speaker sounded better." It becomes a note, filed by the agent that keeps the notes.
- **It remembers.** Tell it something once and it knows it next time, the way our desk robot Friendly does.

The device itself is a **body**, not a brain. The agent lives on your server, built the same way as Friendly, the agent in our Reachy Mini desk robot: a persona, a memory, access to your second brain, and a harness doing the thinking (Claude Code, Hermes or another). The companion is the microphone, the speaker, the face, a motion sensor and two buttons you can find without looking.

## A day with it

Morning. The ghost is on the desk next to the keyboard. You pick it up and its face wakes. Hold the right button: "What did I decide about the speaker yesterday?" It finds the note and reads you the answer. "Let me talk to the researcher." The voice and the face change, and you ask about the paper you saved last week.

Afternoon. You're out, it's on your keys. You remember something on the walk. Hold, "Remember to measure the speaker pocket before printing," let go. A short chime, and the note is filed. You never opened your phone.

Evening. Back on the desk, plugged in to charge. Its face is calm: nothing waiting, notes up for the week. That face is the whole interface when you don't want to talk to it.

<figure class="post-fig wide"><img src="/static/img/blog/argo-companion/02-keychain.webp" alt="The same frosted ghost device hanging from a steel keyring with a brass house key, held up in a hand outdoors at golden hour. Its screen shows a sound-wave icon as if it is speaking." width="1200" height="896" loading="lazy"><figcaption><span class="fig">Fig. 2.</span> Concept render: on a keyring, talking.</figcaption></figure>

## The controls

Two buttons, one on each side, where your thumb and finger land when you hold it.

| Button | Tap | Hold |
|---|---|---|
| **Talk** (right) | Repeat the last answer, or **yes** to a question | Push to talk |
| **Mode** (left) | Next screen, or **no** to a question | Save a moment: one photo and a timestamp |
| **Both** | | 1 s: private (mic and camera off). 3 s: power off |

The motion sensor adds three gestures with no buttons: pick it up to wake it, lay it face down to make it private, shake it to stop it talking.

The two lights under the screen are its cheeks. One of them is always the recording light, so the people around you can see when it's listening.

## What's inside

The companion reuses the electronics from the Exo brooch, the clip-on capture device we've been building for the Exo wearable, with two sensors removed and a speaker added.

<figure class="post-fig wide"><img src="/static/img/blog/argo-companion/03-exploded.webp" alt="Exploded view of the companion on a dark background: the frosted ghost-shaped front shell, a small OLED screen showing a ghost, two pink LEDs and a gold button, a thumb-sized microcontroller board with a tiny camera on a ribbon, a purple amplifier board, a round speaker, and the back plate holding a flat battery with a keyring loop at the top." width="1200" height="896" loading="lazy"><figcaption><span class="fig">Fig. 3.</span> Concept render: what's inside, front to back.</figcaption></figure>

| Part | What it is | Why |
|---|---|---|
| **Brain** | Seeed XIAO ESP32-S3 Sense, a thumbnail-size board with Wi-Fi, a microphone and a camera | Small, cheap, and enough memory to record your question while Wi-Fi reconnects |
| **Face** | 0.96 inch monochrome OLED, 128 × 64 pixels | The pet, the current agent, the answer as text |
| **Voice** | A small I2S amplifier (MAX98357A) and a round speaker | So it can talk, not just show text |
| **Ears** | The microphone on the Sense board | Push to talk, so it only listens while you hold the button |
| **Eye** | The camera, below the screen like a heart | Off unless you ask for a photo |
| **Cheeks** | Two LEDs | Status and recording |
| **Sense of touch** | A motion sensor (LSM6DSV16X) | Wakes when you pick it up, goes private face down |
| **Fuel gauge** | A small battery-gauge chip | An honest battery percentage |
| **Power** | A 500 mAh lithium polymer cell, charged over USB-C | About four days of normal use by our estimate; we'll measure it |
| **Shell** | Clear PETG, printed on our own printer, snap-fit, three feet, a keyring loop | Stands on a desk or hangs from keys; you can see the parts inside |

What we took out: the brooch's air-quality sensor. The speaker needed its room, and an air sensor in your pocket measures your pocket. It will come back in a desk dock instead.

## How it reaches your second brain

The companion joins your Wi-Fi, or your phone's hotspot when you're out. By default it talks to your server, the one machine that's always on and reachable from anywhere, which runs the agent, its memory and your second brain. If you have a home server too, it can use that instead: faster and fully local while you're home. Both share one memory, so it's the same companion either way. On our home server the desk robot's loop already transcribes in under half a second and speaks its first words in about a second; the agent's full answer takes a few seconds more.

Three rules hold the whole way:

- **No keys and no files on the device.** It sends taps and recordings and receives speech and pictures. Anything that moves money is approved elsewhere.
- **The agent and its memory run on machines you control**, not a third-party voice assistant.
- **It speaks out loud, so it's careful what it says.** Public material anywhere, private notes only on your home network, confidential material never.

## Why a ghost

Because it's a little presence that lives with your notes and follows you around. A ghost reads as friendly, it stands up on its own, and a clear shell lets you see that there's nothing hidden inside, just a board, a battery, a speaker and a screen. It's also small enough that you'll actually carry it.

## What's built and what's next

- **Built:** the parts are on the bench (the brain, screen, motion sensor, buttons, lights and battery from the brooch), the ghost shell exists as a 3D model, and the agent host with memory already runs for our desk robot.
- **Next:** the breadboard build with the speaker and battery, real current measurements, the firmware port so the board can hold a conversation, the agent host on our server, and then a printed case.
- **Not yet decided:** the final speaker size, and which of your other agents' news it should tell you about.

We'll post the breadboard build here, with real numbers, when it runs. If you'd want one on your desk or your keys, tell us on X at https://x.com/myargoquest or join the Exo waitlist.

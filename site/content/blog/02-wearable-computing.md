---
title: Sixty-Five Years of Wearable Computers, and Why Now
slug: wearable-computing-why-now
date: 2026-10-07
order: 2
summary: From a roulette computer hidden in a shoe to AI glasses outselling expectations: what wearable computing got wrong for decades, what changed in the last three years, and the bet Exo makes.
---

The first wearable computer was built to beat a casino. In 1961 Edward Thorp and Claude Shannon finished a device the size of a pack of cards: one person timed the roulette wheel with a switch in his shoe, and the other heard the predicted octant as tones in a hidden earpiece. In tests it gave them roughly a 44% edge. In the casino the wires kept breaking.

That's been the story of wearable computing ever since. The idea works, and the hardware, the input method or the social reception lets it down. This post walks through that history, the three things that changed recently, and why we think Exo arrives at the right moment.

## A short history

| Year | Device | What it proved | What held it back |
|---|---|---|---|
| 1961 | Thorp and Shannon's roulette computer | A hidden computer can feed you information in real time | Fragile wires; one trick |
| 1980s | Steve Mann's WearComp | A person can live with a head-worn camera, display and computer, and share video wirelessly | Heavy, home-built, socially alien |
| 1993 | Thad Starner at the MIT Media Lab | A computer and display can be worn *every day*, for years; the "borgs" took notes and searched them in conversation | One-handed chord keyboards; a niche of researchers |
| 2000s | Bluetooth headsets, BlackBerry | People accept a device on their ear and a computer in their pocket | The phone absorbed everything |
| 2013 | Google Glass Explorer Edition (US$1,500) | Consumer smart glasses are buildable | Privacy backlash over an always-ready camera; weak use cases |
| 2015 | Apple Watch | Wrist computing goes mainstream, led by health | A companion to the phone, not a replacement |
| 2016 | AirPods | Always-in audio is socially normal | Audio only |
| 2023 | Ray-Ban Meta glasses | Glasses with a camera, mics and an AI assistant can look like normal glasses | No display |
| 2024 | Humane AI Pin, Rabbit R1, Apple Vision Pro | Voice-first AI hardware has demand; spatial computing is real | The Pin and R1 were slow and closed; Vision Pro is heavy and expensive |
| Feb 2025 | Humane shuts the AI Pin down, sells to HP for US$116M | When a closed device's company leaves, the device stops working | All data deleted on 28 February 2025 |
| Sep 2025 | Meta Ray-Ban Display (US$799) with the EMG Neural Band | A display in ordinary-looking glasses, controlled by finger movements read at the wrist | Closed platform |
| 2025 | Meta and EssilorLuxottica sell about 7 million AI glasses in the year | Demand is real: up from about 2 million in the prior 16 months | |

## The lessons we took

**Input was always the bottleneck.** Thorp used his toe, Starner used a one-handed chord keyboard, Glass used a touchpad on your temple. None of them let you say what you wanted in your own words and have it done.

**Always-on capture kills trust.** Glass taught the industry that people around you care about a camera that might be recording. Exo's camera and microphone work only when you press a button, like a GoPro, with a beep in your earbuds and a light on the clip.

**A closed device dies with its company.** When Humane's servers went dark, the AI Pin stopped working. Exo's software is open source, its agents run on a server you own, and it runs on a Raspberry Pi you could buy at any electronics shop.

**Don't try to replace the phone.** The Pin and the R1 asked people to give up a device that works. Exo is a work tool for a specific person: someone who builds, trades and self-custodies onchain, and wants their agents with them while they're away from the desk.

## Why now: three things changed

### 1. Voice finally works as an interface

Speech-to-text is now fast and accurate enough to be the main input, not a party trick. Exo uses cloud transcription (Deepgram, with Gemini as a fallback): you hold a button, speak and release, your words become text, and the reply plays in your earbuds. Behind that, language models understand the request well enough to act on it.

### 2. There's something to talk to

Until recently, voice assistants could set timers and play songs. Now there are agents that can read your notes, research a question, write code and propose a transaction. Exo connects to **Hermes**, an open-source agent framework, running on your own server. Your words go to agents you control, not to a platform's assistant.

### 3. The hardware got small and cheap

Display glasses such as the VITURE Pro now look like sunglasses and plug into a USB-C port. A Raspberry Pi 4 runs a full Linux desktop off a phone power bank. Mechanical switches, MEMS microphones and tiny camera boards cost a few dollars each. A wearable computer is now something one person can assemble on a desk in a weekend, which is exactly what we did.

### And a fourth, for our audience: money became programmable

For the people Exo is built for, a lot of their working life is onchain. Wallets are programmable accounts, payments settle in minutes, and an agent can build a transaction as easily as it writes a sentence. That's useful and dangerous in equal measure. A wearable that talks to agents with access to money needs a different kind of safety, which is why every Exo transaction passes a Chainlink CRE check and a physical approve key.

## What Exo is

A computer you wear all day: a Raspberry Pi in a sling, display glasses, earbuds and a handful of buttons. You hold a button and speak. Your words go to your own agents, which file your notes, answer from your second brain, manage a capped crypto wallet and build new tools when you ask. The camera and microphone work only when you press. Nothing records on its own.

It's the same idea Thorp and Shannon had: a computer that whispers useful information while you get on with the real world. Sixty-five years later, the input, the intelligence and the hardware have all caught up.

## Sources

- [Edward O. Thorp (Wikipedia)](https://en.wikipedia.org/wiki/Edward_O._Thorp) and [MIT Museum: Thorp–Shannon roulette computer](https://mitmuseum.mit.edu/collections/object/2007.030.014)
- [IEEE Spectrum: Steve Mann, the accidental engineer who conjured up extended reality](https://spectrum.ieee.org/engineer-conjured-up-extended-reality)
- [Thad Starner (Wikipedia)](https://en.wikipedia.org/wiki/Thad_Starner)
- [The Decoder: Humane ends its AI Pin, sells technology to HP for $116 million](https://the-decoder.com/humane-ends-its-ai-pin-in-ten-days-sells-technology-to-hp-for-116-million/)
- [PCWorld: Meta's new Ray-Ban AI smart glasses are controlled using gestures](https://www.pcworld.com/article/2913509/metas-new-ray-ban-ai-smart-glasses-are-controlled-using-gestures.html)
- [UploadVR: Meta and EssilorLuxottica sold 7 million smart glasses in 2025](https://uploadvr.com/meta-essilorluxottica-sold-7-million-smart-glasses-in-2025/)

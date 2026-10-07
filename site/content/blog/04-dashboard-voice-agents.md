---
title: Inside Exo: The Dashboard, the Voice Loop and the Agents
slug: inside-exo-dashboard-voice-agents
date: 2026-10-07
order: 4
summary: A tour for the onchain operator: every panel on the glasses, the buttons and voice commands that move between them, how push-to-talk replaces the keyboard, and how six open-source Hermes agents share a kanban board and build new chain skills.
---

Exo is built for one kind of person first: the **onchain operator**. You self-custody, you transact every week on Ethereum and a few L2s, you already use AI agents in your work, and you travel between events more than you'd like. You've seen every "AI trades for you" pitch and you don't trust any of them.

This post shows what you actually see and do when you wear Exo: the dashboard in your glasses, how you move around it without a mouse, how your voice becomes the main way you work with AI, and how the agents behind it cooperate.

## The dashboard

The dashboard runs on the wearable itself and shows in the display glasses. It's designed for see-through glasses: a pure black background (which the glasses render as transparent), large type, a central safe area and no more than seven rows a screen. You can also open it from your phone over your private network. It's never on the public internet.

| Panel | What it shows |
|---|---|
| **Talk** | The live conversation with your agents: what you said, what they answered |
| **Agents** | The kanban board: which agent is working on what, results, blocked items |
| **Approvals** | Transactions the Guardian has approved and that wait for your key. The item the key will approve is always pinned at the top, and its headline is the Guardian's plain-English explanation, not the agent's description. The auto-approve state is shown here too |
| **Transactions** | The ledger: every proposal, verdict, explanation and executed transaction |
| **Wallets** | Balances across Ethereum, Base, Arbitrum and Polygon for the agent Safe, the deck's gas key and your watch-only cold wallet, read live through NOWNodes |
| **CRE** | The Guardian's handlers and its recent verdicts |
| **Sensors** | The device itself: whether the camera clip is online, free storage, and readings from any sensors you've added |
| **Media** | Photos, voice memos and clips you've captured, and their backup state |

## Moving around without a mouse

There's no trackpad. You have three ways to switch views, and they all do the same thing:

| How | What you do |
|---|---|
| **The P1 button** | Tap for the next panel. Double-tap to flip between the dashboard and the full Linux desktop |
| **Your voice** | "Show approvals", "open wallets", "panel three", "next", "previous", "more" (next page of a list) |
| **The keyboard** (optional) | Keys 1 to 9 jump straight to a panel |

The other buttons each have one job, so you can work them by feel:

| Button | Tap | Double-tap or hold |
|---|---|---|
| **Talk** | Hold to speak, release to send; a press under 0.3 s cancels | Double-tap repeats the last reply |
| **Camera** | Takes one photo | Double-tap starts or stops a video clip |
| **Mic** | Starts or stops a voice memo | |
| **Approve key** | Press and release to approve and send the top transaction | Hold with Mic for 2 s to freeze the wallet |
| **P2** | Programmable: you assign any command to tap, double-tap and hold | |

## Voice is the interface

Holding Talk and speaking replaces most of what you'd type. The loop:

1. **Hold Talk.** Recording starts immediately.
2. **Release.** The audio is transcribed in the cloud (Deepgram, with Gemini as a fallback).
3. **Route.** A small router reads the first words. Navigation ("show approvals", "auto approve off") is handled on the device. A question goes to your main agent and is answered at once. A job becomes a task on the agents' board: "ask the researcher to…", "have the wallet agent…", "note…" (for the librarian) or "build me…" (for the builder).
4. **Reply.** The answer is spoken in your earbuds (Deepgram Aura, or an offline voice when there's no signal) and shown as a line in the glasses.

What changes isn't just speed. You stop translating what you want into clicks. You say "send twenty USDC to Mira for lunch" and hear back "This sends 20 USDC to mira.eth. Nothing else changes." You say "what did I spend on Base this week" and get two sentences. The interface is the conversation.

The camera and mic follow the same rule: they work only when you press. Nothing is captured on its own.

## The agents: open source, on your own server

Behind the voice loop is **Hermes**, an open-source agent framework, running on a cloud server you control and reached over a private Tailscale network. You choose the models: open-weights or commercial models through OpenRouter, or any provider you configure. Exo ships six agent profiles, each with its own personality file and a one-line job description:

| Agent | Job | Can it touch money? |
|---|---|---|
| `librarian` | Files your notes, memos and photo captions into your second brain and answers from it | No |
| `researcher` | Looks things up for you and the other agents: docs, APIs, prices, chain data | No |
| `wallet` | Reads balances, approvals and gas across chains; proposes revokes | Proposes only, through the Guardian |
| `trader` | Proposes trades within your mandate | Proposes only, through the Guardian |
| `portfolio` | Proposes DCA and rebalancing inside your caps | Proposes only, through the Guardian |
| `builder` | Builds small tools on request: dashboard tiles, scripts, new skills, Chainlink CRE handlers | No |

None of them holds a key that can move funds. That's enforced by the Guardian and the Safe module, not by asking the agents nicely.

## How they coordinate: a shared board, not a group chat

The agents work through a **kanban board**, the same way a small team would:

| Need | How it works on the board |
|---|---|
| Hand work to another agent | Create a child task assigned to that agent; the parent waits for it |
| Say something to another agent | Comment on the shared task, so it's on the record |
| Pass a file | Attach it to the task |
| A vague request from you | It lands in **triage**, where a specifier turns it into a clear task before anyone picks it up |
| Tell you it's done | The card moves in your glasses and a notification goes to your Telegram |

A dispatcher picks up ready tasks and starts the assigned agent in its own workspace, with a cap on how many run at once. Every board is a record you can read later: who did what, why, and what they handed to whom.

## New skills that talk to blockchains

Skills are how an agent learns to do something new. Exo ships two chain skills:

- **`nownodes-chain`** reads balances and recent transfers on Ethereum, Base, Arbitrum and Polygon through NOWNodes. It's read-only and never signs.
- **`exo-wallet`** proposes a transaction (a send, or a raw call another agent built) to the Guardian and reads its verdict back to you. It never signs, never holds keys and never retries after a refusal.

The `builder` agent writes more. Ask it for "a tile that shows my Aave health factor" or "a CRE handler that warns me if USDC depegs" and it writes the skill or the Chainlink CRE handler, using Chainlink's own CRE skill for the workflow code. New CRE handlers start **alert-only**: they can warn you, but they can't move money until you've reviewed them.

## Where it stands

The software for all of this was written and reviewed during the TOKEN2049 Origins hackathon (6–7 October 2026), with over 800 automated tests. Speech-to-text, text-to-speech, live NOWNodes reads on all four chains and the Guardian's simulator runs have worked against the real services. The dashboard has been rendered and tested off the device, and the full spoken round trip on the wearable, with the agents' server on the private network, is the next step.

If you're the kind of person who reads the threat model before the feature list, start with our post on [Exo's security model](/blog/security-model.html).

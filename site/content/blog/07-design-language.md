---
title: Designing exo-1: An Instrument, Not a Gadget
slug: design-language
date: 2026-10-07
order: 7
summary: The concept renders for Exo's printed parts: a bone-white and warm-grey body, one signal orange, a glowing green approve key, tiny lowercase labels and exposed screws. Why each choice, what each part does, and what's still only a render.
---

Most crypto hardware looks like a vault, and most AI gadgets look like a pebble. We wanted Exo to look like neither. Exo is something you operate all day, with your hands, often without looking, so we designed it the way good music and lab equipment is designed: as an **instrument**. Every control has one job, one colour and one label, and nothing on the surface is decoration.

These are concept renders, not photographs. We wrote a design brief for the five 3D-printed parts and generated the images from it with an image model, so the people in them are invented and none of these parts has been printed yet. The working prototype today is a bare Raspberry Pi 4, display glasses, earbuds and a breadboard of buttons. The renders are the target that prototype is growing into.

<figure class="post-fig wide"><img src="/static/img/blog/design/m1-hero-still-life.webp" alt="The complete exo-1 kit laid out in a grid on pale grey: the Pi case on its power sled with an orange coiled cable, the glasses case with black display glasses and white earbuds, the remote panel with an orange talk key and a green approve key, the camera clip and a black strap." width="1600" height="893" loading="lazy"><figcaption><span class="fig">Fig. 1.</span> The whole kit, knolled: Pi case and power sled, glasses case, remote panel, camera clip and sling strap.</figcaption></figure>

## The design language

| Choice | What it looks like | Why |
|---|---|---|
| **Bone white and warm grey** | 3D-printed shells in `#EDEBE6` and `#9A9893`, with a few bead-blasted aluminium-look plates | Calm, neutral and honest about being printed. It reads as a tool, not jewellery |
| **One signal orange** | `#FF5A1F`, used only on the most important control (talk) and a couple of pull-tabs | If everything shouts, nothing does. Orange means "this is how you start" |
| **Green means send** | The approve key is a clear mechanical switch that glows green | The one action that moves money gets its own colour, its own shape and its own corner of the panel |
| **Red means recording** | A small red dot labelled `rec` | For the people around you: the camera and mic are visible when they're on |
| **Tiny lowercase labels** | Monospaced labels next to every control and port: `talk`, `cam`, `mic`, `p1`, `p2`, `rec`, `approve`, `usb-c`, `hdmi` | You learn the device by reading it, and the labels still make sense on camera |
| **Exposed screws, visible grid** | Small torx screws at the corners and a faint engraved grid on large faces | It's meant to be opened, repaired and modified. The grid hints at the measurements underneath |
| **Uniform 2 mm radii, flat faces** | Every corner the same radius, crisp seams | Precision is what separates a printed instrument from a printed prototype |
| **A mark, not a theme** | A small hexagon outline beside the model name `exo-1` | Enough to identify it, quiet enough that the device is about you, not the logo |

## The five parts

<figure class="post-fig wide"><img src="/static/img/blog/design/d1-exploded-kit.webp" alt="Exploded isometric drawing of the kit: the Pi case lid, fan, board and body above the power bank sled; the remote panel with its keys lifted off; the camera clip split into lens ring, shell and clip; the glasses case open with glasses, earbuds and adapter; and the strap." width="1600" height="893" loading="lazy"><figcaption><span class="fig">Fig. 2.</span> Exploded view of the kit, numbered 01 to 05.</figcaption></figure>

### 01 · Pi case and power sled

A flat slab around the Raspberry Pi 4: a bone-white body, a light-grey vented lid over a small fan, and every port labelled on one long side. The power bank rides underneath in a grey sled that latches on, joined by a short coiled orange USB-C cable, with a battery scale printed on the side. The ribbon cable to the remote leaves through a rubber grommet, and two webbing slots let the slab ride in a pouch at your hip.

<figure class="post-fig wide"><img src="/static/img/blog/design/d2-pi-case-orthographic.webp" alt="Orthographic drawing of the Pi case and power sled: top, front, side and rear views, an isometric view and a section showing airflow through the vent grid, with labels for the ports, fan, latch, webbing slots, ribbon grommet and battery scale." width="1600" height="893" loading="lazy"><figcaption><span class="fig">Fig. 3.</span> Pi case and sled, orthographic sheet. The section view shows air drawn up through the vent grid by the fan.</figcaption></figure>

<figure class="post-fig wide"><img src="/static/img/blog/design/m7-pi-case-macro.webp" alt="Close-up of a corner of the Pi case: the grey perforated lid, a torx screw, a small hexagon outline and exo-1 printed in lowercase, the grey ribbon cable leaving through a black grommet, and an orange pull-tab." width="1600" height="893" loading="lazy"><figcaption><span class="fig">Fig. 4.</span> The corner detail: vent grid, torx screw, model mark, ribbon grommet and pull-tab.</figcaption></figure>

### 02 · Remote panel

The part you touch most, clipped to the strap at chest height under your thumb. The layout follows how often you use each control. **Talk** is the biggest key and the only orange one. **Cam** and **mic** are grey. **P1** and **P2** are small and black because they're programmable. The **rec** light sits where bystanders can see it. And alone at one end, on its own raised pad, is the **approve** key: a mechanical switch in a clear housing that glows green when a transaction is waiting.

<figure class="post-fig wide"><img src="/static/img/blog/design/d3-remote-panel-blueprint.webp" alt="Layout sheet of the remote panel: an orange talk key, grey cam and mic keys, black p1 and p2 keys, a red rec light, and a green-lit approve keyswitch on its own pad, numbered 01 to 05, with a side profile showing key heights and the strap clip." width="1600" height="893" loading="lazy"><figcaption><span class="fig">Fig. 5.</span> Remote panel layout. 01 talk: hold to speak. 02 cam: tap for a photo. 03 mic: tap for a memo. 04 approve: send the next transaction. 05 rec: recording light.</figcaption></figure>

<figure class="post-fig tall"><img src="/static/img/blog/design/m4-approve-key-macro.webp" alt="Macro photograph of a fingertip about to press the clear approve keyswitch glowing green on a grey aluminium panel clipped to a black strap, with the orange talk key and a red rec light in the foreground." width="1289" height="1600" loading="lazy"><figcaption><span class="fig">Fig. 6.</span> The approve key. It's the one control that moves money, so it looks, feels and sounds different from everything else.</figcaption></figure>

The approve key is the design's centre of gravity. In software, approving a payment is a button that looks like every other button. On Exo it's a physical switch with travel, a click and a light, set apart from everything else, and nothing an AI agent writes can press it. Our post on [NOWNodes and the approve key](/blog/nownodes-and-the-approve-key.html) explains the security reasons. The design reason is simpler: the most consequential action should be the most deliberate one.

### 03 · Glasses case

A bone-white clamshell held shut by a black rubber band, with a felt cradle for the display glasses, a post to wind the cable around, and a labelled pocket for the HDMI-to-USB-C adapter. It's the part people see when you sit down, so it's the most "finished" object in the kit.

<figure class="post-fig wide"><img src="/static/img/blog/design/d4-glasses-case-sheet.webp" alt="The glasses case open, with black display glasses in a grey felt cradle, the cable wound on a post and an adapter in a felt pocket, beside the closed case with a black rubber band and exo-1 printed on the lid." width="1600" height="893" loading="lazy"><figcaption><span class="fig">Fig. 7.</span> Glasses case, open and closed.</figcaption></figure>

### 04 · Camera clip

A bone-white cylinder about the size of a thumb tip, with a black lens ring, a tiny orange index dot so you know which way is up, a spring clip for a collar, strap or placket, and a USB-C port underneath. It takes a photo or a clip only when you press `cam`, and the `rec` light tells everyone when it does.

<figure class="post-fig wide"><img src="/static/img/blog/design/d5-camera-clip-anatomy.webp" alt="Anatomy drawing of the camera clip: an exploded view of the lens ring, front shell, camera board, inner frame, back shell and spring clip, a cross-section of it gripping a strap, and three small views of it on a collar, a strap and a shirt placket." width="1600" height="893" loading="lazy"><figcaption><span class="fig">Fig. 8.</span> Camera clip anatomy, and three ways to wear it.</figcaption></figure>

### 05 · Sling strap

A black nylon sling worn across the body, with a grey buckle. The remote clips to it at the chest, the camera sits on the collar, and the Pi slab rides in a pouch at the back hip, with the ribbon cable running inside the strap.

<figure class="post-fig wide"><img src="/static/img/blog/design/m7b-detail-grid.webp" alt="Five product close-ups in a grid: the Pi case corner with an orange pull-tab, the remote panel with its keys and glowing approve switch, the power sled with its coiled orange cable, the glasses case open with glasses, and the camera clip." width="1600" height="893" loading="lazy"><figcaption><span class="fig">Fig. 9.</span> Details: Pi case, remote panel, power sled, glasses case, camera clip.</figcaption></figure>

## Made to be printed

In the renders every shell fits on one 256 × 256 mm printer bed, in two filament colours plus a few small orange parts. That's the aim for the real parts too: flat faces that print without supports where they show, and screws rather than glue, so anyone with a printer can make, repair or remix their own.

<figure class="post-fig wide"><img src="/static/img/blog/design/d7-print-plate.webp" alt="Overhead view of a black printer build plate holding every printed part laid flat, each with a small label: Pi case body and lid, power sled, glasses case top and bottom, talk key cap, pull-tab, camera clip front and back, remote panel front and back." width="1600" height="1195" loading="lazy"><figcaption><span class="fig">Fig. 10.</span> One build plate, every part.</figcaption></figure>

## Worn

The kit is meant to disappear into what you're already wearing. The strap goes over a sweater or a jacket, the remote sits where your thumb falls, and the only bright points are the orange talk key and, when something waits for you, the green approve key.

<figure class="post-fig tall"><img src="/static/img/blog/design/m2-woman-talk-portrait.webp" alt="Studio portrait of a woman in a grey sweatshirt wearing display glasses, earbuds, the camera clip on her collar and the sling strap, pressing the orange talk key on the remote panel at her chest while she speaks." width="1289" height="1600" loading="lazy"><figcaption><span class="fig">Fig. 11.</span> Hold talk, speak, release.</figcaption></figure>

<figure class="post-fig wide"><img src="/static/img/blog/design/m3-man-city-walk.webp" alt="A man in a stone-coloured jacket walking across a concrete plaza, wearing display glasses, an earbud, the camera clip on his collar and the sling strap with the remote panel at his chest and the grey pouch at his hip." width="1600" height="893" loading="lazy"><figcaption><span class="fig">Fig. 12.</span> On the move: the remote at the chest, the slab at the hip.</figcaption></figure>

<figure class="post-fig wide"><img src="/static/img/blog/design/m5-cafe-glasses-case.webp" alt="A man at a café table by a window lifting display glasses out of the open glasses case, with the Pi case, power bank, an espresso and the remote panel on the table and the camera clip on his shirt." width="1600" height="893" loading="lazy"><figcaption><span class="fig">Fig. 13.</span> Setting up at a café table.</figcaption></figure>

<figure class="post-fig wide"><img src="/static/img/blog/design/m6-silhouettes-orange.webp" alt="Two people in light grey and white standing in profile facing each other on a bright orange backdrop, both wearing dark display glasses and the sling strap with the remote panel and its green approve key at their side." width="1600" height="893" loading="lazy"><figcaption><span class="fig">Fig. 14.</span> Two operators, on signal orange.</figcaption></figure>

## Two visual directions

This site is set like a 19th-century scientific volume, in Argo's ink and paper. The renders explore a second direction for the hardware itself: bone, grey and one orange. We think they work together, the way a lab instrument sits on the pages of its manual, but it's an open question, and we'd like to hear which you prefer.

## What's real and what's a render

| | Status |
|---|---|
| The five parts, their controls and how they're worn | Real design decisions, matching the prototype's pin map and button software |
| The colours, materials, labels and the `exo-1` name | Concept. `exo-1` is a working name, not a final product name |
| Every image on this page | Generated from our written design brief with an image model, with the first exploded view used as the reference for the rest so the parts stay consistent. The people are invented |
| Printed parts | Not printed yet. Small printed labels may need a second filament colour or pad printing |
| The approve key | A real part we already have: a clear mechanical keyswitch with a green LED. Its wiring to the Pi is specified and its software is written and tested |

The renders are a promise about how Exo should feel in the hand: deliberate, legible and a little playful. The next step is to print them and find out where the promise and the printer disagree.

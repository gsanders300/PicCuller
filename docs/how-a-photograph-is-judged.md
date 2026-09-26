# How a Photograph Is Judged

This document follows one photograph all the way through Photo Cull: from the moment the
program notices the file to the moment it decides whether that picture makes the cut.

It explains reasoning; it does not specify rules. To get started, read the
[user guide](../readme.md). For every option and output column, see the
[reference](reference.md). For the exact rules, read
[Specifications.md](../Specifications.md). To understand what actually happens to your
pictures and why, start here.

**The most important thing to know first:** Photo Cull never deletes anything.

In photography, "culling" usually means throwing pictures away. Here it does not. The
program copies its favorites into a new folder and leaves every original exactly where it
was, untouched.

A photograph that gets "culled" in this program has simply not been copied. It is still on
your drive, unharmed. Everything below happens to a read-only original.

Our example is a RAW file called `DSC_4471.NEF`. It lives in
`/photos/2026-summer/day-two/`, and it is one frame in a burst of nine shots of a heron
taking off.

---

## Step 1: Getting noticed

You point the program at a folder. It walks through that folder and every folder inside
it, looking for files it recognizes as images.

Depth does not matter. Our heron photo is four levels down, and it is found like any other.

A few things are skipped on purpose:

- **Shortcuts (symbolic links).** Following one could send the program into unrelated
  folders, or into a loop.
- **Its own output folders.** Yesterday's exported picks must never be judged again as if
  they were new originals.
- **Operating-system housekeeping folders**, such as `.Trashes` and `$RECYCLE.BIN`. These
  hold deleted or duplicate copies.
- **The `._` companion files** a Mac scatters across memory cards. They look like photos
  but contain no picture at all.

If the program cannot read a folder, perhaps because of a permissions problem, it says so
and writes a note in `failures.csv`. It does not pretend the folder was empty.

That distinction matters. Silently skipping files would mean you are choosing from a
smaller pool than you think.

## Step 2: Joining a family

The program groups files that describe the same photograph into one **asset family**.

Cameras often write two files for one press of the shutter: a RAW file and a JPEG. Some
editing programs add a third, a small sidecar file that holds your edits. All of them are
one photograph.

The program analyzes only one file per family. Judging every member separately would waste
effort and clutter the results.

By default it picks the RAW, because RAW holds the most information. Our `DSC_4471.NEF` is
the chosen one, while `DSC_4471.JPG` waits in the background.

The rest of the family is not forgotten. If this photograph is selected, every member is
exported together, sidecar files included. You never end up with a RAW in your picks folder
and its edits left behind.

## Step 3: Checking whether we have met before

Before doing any real work, the program checks whether it has already judged this exact
file. Analysis is expensive, so reusing earlier results saves a lot of time.

It looks the photograph up in a small database using three things:

- the file's location inside your collection
- its size in bytes
- the moment it was last modified, measured to the nanosecond

If all three match a stored record, and the models and scoring version have not changed,
nothing about the pixels can have changed. The program reuses the numbers it calculated
last time.

Two details make this more useful than it sounds:

- **The location is relative to your collection**, not a full path. You can move the whole
  shoot to a different drive and the stored work still applies.
- **Measurements are stored apart from preset-specific judgments.** Switching from the
  wildlife preset to the landscape preset reuses almost everything instead of starting
  over.

If we have met this photograph before, it skips ahead to Step 8. Our heron photo is new, so
it continues.

## Step 4: Reading the label

Next, the program reads the information the camera wrote alongside the picture.

The most important item is the moment of capture, accurate to fractions of a second. That
timestamp is what later lets the program work out which frames belong to the same burst.

It also reads:

- the camera model and serial number
- the frame's sequence number
- which autofocus points the camera used, when available

The camera identity matters when you shoot one event with two bodies. The program must not
group a frame from one camera with a frame from the other.

Timestamps need care. A camera usually records local time, and sometimes records the time
zone offset too.

When the offset is missing, the program uses the zone you supply. If you supply none, it
uses the computer's own zone as a last resort. Either way, it records which choice it made.

That honesty matters. If two frames look an hour apart only because of a time zone mix-up,
the burst grouping would be wrong, and you would want to know why.

## Step 5: Opening the picture

Now the program decodes the image: it turns the compressed file into actual pixels it can
measure.

For a RAW file like ours, it first asks for the preview JPEG the camera embedded in the
file. Cameras include it so the back screen can show the shot instantly. Using it is much
faster than rebuilding the image from sensor data.

If no preview exists, the program builds a half-size version itself.

Two things happen during decoding that affect everything afterward.

**It asks for a reduced decode.** It only needs an image about 1024 pixels on its longest
side. There is no reason to unpack all 45 million pixels and then throw most of them away,
so it asks for the smaller version up front. The embedded preview is often full sensor
resolution, so it gets the same reduced decode.

**It applies the rotation the camera recorded.** If you turned the camera sideways for a
vertical shot, the file often stores the pixels in the original orientation plus a note
saying "rotate this."

Every measurement that follows happens after that rotation. Measured sideways, a vertical
portrait would be analyzed as though it were a landscape, and the results would be subtly
wrong.

The program decodes each photograph exactly once. It reuses that single decoded copy for
every measurement below, rather than reopening the file five times.

## Step 6: Taking measurements

With pixels in hand, the program takes four direct measurements. No artificial
intelligence is involved yet; these are plain calculations.

### Sharpness

The program converts the image to grayscale and applies a filter that responds strongly to
edges and weakly to smooth areas. A crisp photograph is full of sharp edges; a blurry one
is mostly smooth gradients.

It does not average this across the whole frame. It divides the image into a grid of 256
tiles and keeps only the sharpest three percent of them.

That choice is deliberate. A heron against a plain sky is mostly smooth, empty sky.
Averaging the whole frame would score it as blurry even if the bird is tack sharp.

By looking only at the sharpest few tiles, the program asks a better question: "is the
sharpest part of this photograph actually sharp?"

### Exposure

The program counts pixels that have lost detail at either extreme:

- A pixel is **blown** when any one of its red, green, or blue channels has hit its
  maximum.
- A pixel is **crushed** only when all three channels have bottomed out.

The asymmetry is intentional. A brilliant red sunset can max out the red channel while
green and blue stay moderate, and that red detail is genuinely gone.

Meanwhile, a saturated blue sky legitimately has almost no red in it, and that is not a
defect. So highlights are judged channel by channel, while shadows require the whole pixel
to go black.

### A visual fingerprint

The program computes a short code, called a perceptual hash, that summarizes what the image
looks like. Two photographs of nearly the same scene produce nearly the same code.

This becomes useful in Step 9.

### Faces and eyes (portrait preset only)

If you chose the portrait preset, the program looks for faces, and then for open eyes
within them.

If it finds a face but too few eyes, it flags the photograph as a possible blink.

If it finds no face at all, it says so but applies no penalty. A missed face usually means
the subject was turned sideways or backlit, which says nothing about whether the photograph
is good.

## Step 7: Asking the neural networks

Now three machine learning models weigh in. All of them run on your own computer; nothing
is uploaded anywhere.

### Technical quality

A model called MUSIQ produces a single quality score. It was trained on a large collection
of photographs that people rated for quality, and it responds to things like noise,
softness, and compression damage.

### Aesthetic appeal

A model called CLIP converts the photograph into a list of 768 numbers that together
describe its visual content.

A second, smaller model reads those numbers and predicts an aesthetic score. It was trained
on photographs that people rated for beauty.

Treat this number with appropriate skepticism. It reflects the average taste of the people
who produced the training data. It is a useful first filter, not a verdict on your work.

### Subject framing (wildlife, portrait, and landscape presets)

The program compares the photograph against two written descriptions, one good and one bad.
Whichever description fits better nudges the score.

For wildlife, these are roughly "the complete animal clearly in frame" versus "the animal is
cut off or leaving the frame."

This is a rough heuristic, not an object detector, and it can be fooled.

That list of 768 numbers is saved, because Steps 9 and 12 both use it.

## Step 8: Comparing against the whole shoot

Here a single photograph stops being judged on its own. The program ranks every photograph
in the run by sharpness and converts each one's position into a percentile.

The reason is that a raw sharpness number means nothing by itself. A value like 1,240 depends
entirely on how much fine detail the scene contained.

Our heron photo lands in the 78th percentile: it is sharper than 78 percent of the frames in
this shoot. That percentile, not the raw number, feeds the final score.

Plan around one consequence. The comparison covers everything in one run, so a folder with
several unrelated shoots gets judged all together.

If you cull a whole summer at once, a sharp indoor shot competes against sharp landscapes.
To judge shoots independently, run them separately.

## Step 9: Finding the burst

The program now works out which frames belong to the same burst. Within a burst most frames
are nearly identical, and picking the best one is the most valuable thing this program does.

It sorts every photograph by capture time, then walks through the list. Each frame joins the
group before it only when all of these hold:

- It was taken within two seconds of the previous frame.
- The group has not already stretched past ten seconds in total.
- Its camera identity does not conflict with the previous frame's.
- Either the visual fingerprints from Step 6 are very close, or the CLIP descriptions from
  Step 7 are very similar.

The last condition is the interesting one. Timing alone is not enough: you might fire two
seconds apart while swinging to a completely different subject. A visual test means a burst
has to actually look like a burst.

There are two similarity tests because they fail differently:

- **The fingerprint** is fast and catches near-duplicate framing.
- **The CLIP comparison** understands content and survives moderate changes in brightness
  or position.

The ten second ceiling stops a long chain of gradual changes from merging into one huge
group. Without it, a hundred frames shot over two minutes could link together one pair at a
time, and you would get a single winner from two minutes of shooting.

Our photograph joins a burst of nine frames. The program then compares its sharpness against
only the other eight. This **relative sharpness** figure shows our frame reaches 94 percent
of the sharpest frame in its burst.

## Step 10: The score

Everything now collapses into one number. The program multiplies the measurements together,
and raises each factor to a power that controls how much it matters:

```
score = aesthetic appeal
      × (sharpness percentile ^ its weight)
      × (relative sharpness in burst ^ its weight)
      × (technical quality ^ its weight)
      × (exposure health ^ its weight)
      × (eye factor ^ its weight)
      × (subject framing ^ its weight)
```

Multiplying rather than adding is a real design decision. Every factor except aesthetic
appeal is a number between zero and one, so each acts as a penalty that can only pull the
score down.

Because the factors multiply, one very poor factor drags the whole score down, however good
everything else is. A gorgeous, perfectly exposed, beautifully composed photograph that is
out of focus scores badly, which is intended.

If the factors were added instead, strong scores elsewhere could outvote the blur.

The weights differ by preset, which is how presets express different priorities:

- **Landscape** leans harder on sharpness and exposure, because a landscape usually should
  be sharp throughout.
- **Wildlife** leans harder on relative sharpness within the burst. Across a burst of a
  moving animal, the real question is which frame caught it best.

One honest caveat: these weights were chosen by judgment, not derived from data. Nobody has
yet tuned them against a large set of your own keep and reject decisions. Treat the scores
as a useful ordering, not as truth.

## Step 11: Three different rankings

The program now produces three separate rankings. They are easy to confuse, so they are
kept strictly apart:

- **Burst rank** orders the frames inside one burst. Our photograph places second of nine.
  Rank one in each burst is that burst's **winner**.
- **Global quality rank** orders every photograph in the run against every other. Our
  photograph places 340th out of 1,200.
- **Selection rank** orders only the burst winners against each other. This is the
  shortlist: one frame per burst, sorted best first.

Our heron photo's journey ends here. It placed second in its burst, so it lost to the frame
beside it and never reaches the shortlist. Its neighbor, `DSC_4470.NEF`, was very slightly
sharper and goes forward instead.

Each photograph also gets a one-line reason in plain words. For our heron it reads something
like `2nd of 9 in burst; scored 3% lower than DSC_4470.NEF, mainly on sharpness`.

The terminal shows the same kind of line beside each shortlisted winner. You can see why a
frame won without opening a spreadsheet.

Before anything else, the program writes every one of these numbers to `evaluation.csv`, one
row per photograph. It does this before asking you any questions, so an interruption cannot
throw away finished work.

## Step 12: Your say

You do not have to accept any of this.

**A feedback file overrides the program.** Marking a photograph "keep" forces it into the
selection even if it lost its burst, which is exactly what our heron photo would need.
Marking one "reject" removes it even if it won.

Keeps count toward the number you ask for, and they can push the selection above it.
Honoring your explicit choice matters more than hitting a target count.

**The program asks how many photographs you want.** You can answer with a number, with
"all," or with nothing at all if you only want the report.

**If you ask for variety, it does more than take the top N.** Using the CLIP descriptions
from Step 7, it balances quality against novelty. Each pick weighs how good a photograph is
against how different it is from what has already been chosen.

That way, selecting thirty photographs does not hand you thirty near-identical frames of the
same heron.

Whatever the path, the final list is sorted by score. The best photograph is always first,
and any star rating derived from position agrees with measured quality.

## Step 13: Leaving with the picks

For the photographs that made it, the program copies each one's whole asset family into a
picks folder.

It plans the entire operation before touching a single file. It checks that:

- no two files would land on the same destination name
- every source really sits inside the folder you named
- there is enough free space

Only when the complete plan passes every check does copying begin.

Copies go into a hidden staging folder, each file under a temporary name that is renamed
into place once it is complete. The picks folder appears only after every copy succeeds; a
failed export removes the staging folder.

So you never find a half-written photograph that looks complete, or a half-finished picks
folder. The program also refuses to overwrite anything that already exists.

Folder structure is preserved. `day-two/DSC_4470.NEF` arrives at
`picks/day-two/DSC_4470.NEF` instead of being dumped into one flat pile.

If you asked for star ratings, they go into small sidecar files **next to the copies**,
never next to your originals. Your source folder ends the operation exactly as it began.

The top ten percent of the selection get five stars, the next thirty percent get four, and
the rest get three.

## What happened to our photograph

`DSC_4471.NEF` was discovered, grouped with its JPEG, and decoded once. It was measured for
sharpness and exposure, scored by three neural networks, and ranked against 1,199 other
frames. It was matched into a burst of nine and beaten by a neighboring frame that was very
slightly sharper.

It was not selected. It was also not deleted, moved, renamed, or altered in any way. It sits
exactly where it always sat.

Every number the program calculated about it is recorded in `evaluation.csv`. You can sort
that file yourself, disagree with the outcome, and put the photograph back into the
selection with a one-line feedback entry.

That is the whole point. The program is fast at the boring part: looking at 1,200 frames and
noticing that nine of them are the same heron. You remain the one who decides which heron
photograph you actually wanted.

# How a Photograph Is Judged

This document follows one photograph all the way through Photo Cull, from the moment the
program notices the file to the moment it decides whether that picture makes the cut.

It is written as an explanation rather than a specification. If you want the exact rules,
read `Specifications.md`. If you want the command reference, read `readme.md`. If you want
to understand what actually happens to your pictures and why, start here.

**The single most important thing to know first:** Photo Cull never deletes anything. In
photography, "culling" usually means throwing pictures away. Here it does not. The program
copies its favorites into a new folder and leaves every original exactly where it was,
untouched. A photograph that gets "culled" in this program has simply not been copied. It
is still sitting on your drive, unharmed. Everything below happens to a read-only original.

Our example photograph is a RAW file called `DSC_4471.NEF`. It lives in
`/photos/2026-summer/day-two/`, and it is one frame in a burst of nine shots of a heron
taking off.

---

## Step 1: Getting noticed

You point the program at a folder. It walks through that folder and every folder inside
it, no matter how deeply nested, looking for files it recognizes as images. Our heron photo
is four levels down, and that does not matter at all.

A few things get skipped on purpose. The program will not follow a shortcut (a symbolic
link) that points somewhere else, because that could send it wandering off into unrelated
folders or even into a loop. It ignores its own output folders, so yesterday's exported
picks never get judged a second time as if they were new originals. It skips the
housekeeping folders your operating system creates, such as `.Trashes` and `$RECYCLE.BIN`,
because those hold deleted or duplicate copies. It also skips the little `._` companion
files that a Mac scatters across memory cards, which look like photos but contain no
picture at all.

If the program cannot read a folder, perhaps because of a permissions problem, it says so
and writes a note in `failures.csv`. It does not quietly pretend the folder was empty. That
distinction matters: silently skipping files would mean you are choosing from a smaller pool
than you think you are.

## Step 2: Joining a family

Cameras often write two files for a single press of the shutter: a RAW file and a JPEG. Some
editing programs add a third, a small sidecar file holding your edits. All of these describe
one photograph, so the program treats them as one **asset family**.

Judging every family member separately would waste effort and clutter the results, so the
program picks one file per family to actually analyze. By default it picks the RAW, because
RAW holds the most information. Our `DSC_4471.NEF` is the chosen one, while
`DSC_4471.JPG` waits quietly in the background.

The rest of the family is not forgotten. If this photograph is eventually selected, every
member of the family gets exported together, sidecar files included. You never end up with
a RAW in your picks folder and its edits left behind.

## Step 3: Checking whether we have met before

Analyzing a photograph takes real work, so before doing any of it the program checks
whether it has already judged this exact file.

It looks the photograph up in a small database using three things: the file's location
inside your collection, its size in bytes, and the moment it was last modified, measured to
the nanosecond. If all three match a stored record, nothing about the pixels can have
changed, so the program reuses the numbers it calculated last time.

Two details make this more useful than it sounds. First, the location is stored relative to
your collection rather than as a full path, so you can move the whole shoot to a different
drive and the stored work still applies. Second, the stored measurements are separated from
the preset-specific judgments, so switching from the wildlife preset to the landscape preset
reuses almost everything instead of starting over.

If we have met this photograph before, it skips ahead to Step 8. Our heron photo is new, so
it continues.

## Step 4: Reading the label

Before looking at the picture, the program reads the information the camera wrote alongside
it. The most important item is the moment of capture, accurate to fractions of a second,
because that timestamp is what later lets the program work out which frames belong to the
same burst.

It also reads the camera model and serial number, the frame's sequence number, and, when
available, which autofocus points the camera used. The camera identity matters because if
you shot the same event with two bodies, the program must not accidentally group a frame
from one camera with a frame from the other.

Timestamps are handled carefully. A camera usually records local time, and sometimes records
the time zone offset alongside it. When the offset is missing, the program uses the zone you
supply, or the computer's own zone as a last resort, and it records which of those it did.
That honesty matters: if two frames are one hour apart only because of a time zone
misunderstanding, the burst grouping would be wrong, and you would want to know why.

## Step 5: Opening the picture

Now the program decodes the image, meaning it turns the compressed file into actual pixels
it can measure.

For a RAW file like ours, it first asks for the small preview image the camera embedded in
the file. Cameras include these so the back screen can show you the shot instantly, and
using it is much faster than reconstructing the full image from sensor data. If no preview
exists, the program falls back to building a half-size version itself.

Two things happen during decoding that affect everything afterward.

First, the program asks for a reduced decode. It only needs an image about 1024 pixels on
its longest side, so there is no reason to unpack all 45 million pixels and then throw most
of them away. Asking for the smaller version up front is considerably faster.

Second, and more importantly, it applies the rotation the camera recorded. If you turned the
camera sideways for a vertical shot, the file often stores the pixels in the original
orientation plus a note saying "rotate this." Every measurement that follows happens after
that rotation is applied. If the program measured the sideways version, a vertical portrait
would be analyzed as though it were a landscape, and the results would be subtly wrong.

The program decodes each photograph exactly once and reuses that single decoded copy for
every measurement below, rather than reopening the file five times.

## Step 6: Taking measurements

With pixels in hand, the program measures four things. None of these involve artificial
intelligence yet; they are direct calculations.

**Sharpness.** The program converts the image to grayscale and applies a mathematical filter
that responds strongly to edges and weakly to smooth areas. A crisp photograph is full of
sharp edges; a blurry one is mostly smooth gradients. Rather than averaging this across the
whole frame, the program divides the image into a grid of 256 tiles and keeps only the
sharpest three percent of them.

That choice is deliberate and worth understanding. A photograph of a heron against a plain
sky is mostly smooth, empty sky. Averaging the whole frame would score it as blurry even if
the bird itself is tack sharp. By looking only at the sharpest few tiles, the program asks a
better question: "is the sharpest part of this photograph actually sharp?"

**Exposure.** The program counts pixels that have lost detail at either extreme. A pixel
counts as "blown" when any one of its red, green, or blue channels has hit maximum, and
"crushed" only when all three have bottomed out.

The asymmetry is intentional. A brilliant red sunset can max out the red channel completely
while green and blue stay moderate, and that red detail is genuinely gone. Meanwhile a
saturated blue sky legitimately has almost no red in it, and that is not a defect. So
highlights are judged channel by channel, while shadows require the whole pixel to go black.

**A visual fingerprint.** The program computes a short code called a perceptual hash that
summarizes what the image looks like. Two photographs of nearly the same scene produce
nearly the same code. This becomes useful in Step 9.

**Faces and eyes, sometimes.** If you chose the portrait preset, the program looks for faces
and then for open eyes within them. If it finds a face but too few eyes, it flags the
photograph as a possible blink. If it finds no face at all, it says so but applies no
penalty, because a failure to detect a face usually means the subject was turned sideways or
backlit, which says nothing about whether the photograph is good.

## Step 7: Asking the neural networks

Now three machine learning models weigh in. All of them run on your own computer; nothing is
uploaded anywhere.

**Technical quality.** A model called MUSIQ was trained on a large collection of photographs
that people rated for quality, and it produces a single score. It responds to things like
noise, softness, and compression damage.

**Aesthetic appeal.** A model called CLIP converts the photograph into a list of 768 numbers
that together describe its visual content. A second, smaller model, trained on photographs
that people rated for beauty, reads those numbers and predicts an aesthetic score.

Treat this number with appropriate skepticism. It reflects the average taste of the people
who produced the training data. It is a useful first filter, not a verdict on your work.

**Subject framing, depending on preset.** For the wildlife, portrait, and landscape presets,
the program compares the photograph against two written descriptions, one good and one bad.
For wildlife these are roughly "the complete animal clearly in frame" versus "the animal is
cut off or leaving the frame." Whichever description fits better nudges the score. This is a
rough heuristic, not an object detector, and it can be fooled.

That list of 768 numbers is saved, because Steps 9 and 11 both need it.

## Step 8: Comparing against the whole shoot

Here is where a single photograph stops being judged on its own.

A raw sharpness number is meaningless by itself. A number like 1,240 tells you nothing,
because it depends entirely on how much fine detail the scene contained. So the program
ranks every photograph in the run by sharpness and converts each one's position into a
percentile.

Our heron photo lands in the 78th percentile, meaning it is sharper than 78 percent of the
frames in this shoot. That percentile, not the raw number, feeds the final score.

This has a consequence worth planning around. Because the comparison covers everything in
one run, pointing the program at a folder containing several unrelated shoots means they all
get judged against each other. If you cull a whole summer at once, a sharp indoor shot
competes against sharp landscapes. When you want shoots judged independently, run them
separately.

## Step 9: Finding the burst

Photographers shoot bursts, and within a burst most frames are nearly identical. Picking the
best one is the single most valuable thing this program does, so it needs to work out which
frames belong together.

The program sorts every photograph by capture time and then walks through the list, deciding
whether each frame joins the group that came before it. A frame joins only when all of these
hold true:

- It was taken within two seconds of the previous frame.
- The group has not already stretched past ten seconds in total.
- The camera identity does not conflict with the previous frame's.
- Either the visual fingerprints from Step 6 are very close, or the CLIP descriptions from
  Step 7 are very similar.

That last condition is the interesting one. Timing alone is not enough, because you might
fire two seconds apart while swinging the camera to a completely different subject. Adding a
visual test means a burst has to actually look like a burst. Two ways of measuring similarity
are offered because they fail differently: the fingerprint is fast and catches near-duplicate
framing, while the CLIP comparison understands content and survives moderate changes in
brightness or position.

The ten second ceiling exists to stop a long chain of gradual changes from merging into one
enormous group. Without it, a hundred frames shot over two minutes could link together one
pair at a time, and you would get a single winner from two minutes of shooting.

Our photograph joins a burst of nine frames. Now the program compares its sharpness against
only the other eight, producing a **relative sharpness** figure. Our frame reaches 94 percent
of the sharpest frame in its burst.

## Step 10: The score

Everything now collapses into one number. The program multiplies the measurements together,
with each factor raised to a power that controls how much it matters:

```
score = aesthetic appeal
      × (sharpness percentile ^ its weight)
      × (relative sharpness in burst ^ its weight)
      × (technical quality ^ its weight)
      × (exposure health ^ its weight)
      × (eye factor ^ its weight)
      × (subject framing ^ its weight)
```

Multiplication rather than addition is a real design decision with a real consequence. Every
factor besides aesthetic appeal is a number between zero and one, so each acts as a penalty
that can only pull the score down. Because they multiply, one very poor factor drags the
whole score down no matter how good everything else is. A gorgeous, perfectly exposed,
beautifully composed photograph that happens to be out of focus scores badly, and that is
the intended behavior. If the factors were added instead, strong scores elsewhere could
outvote the blur.

The weights differ by preset, which is how the presets express different priorities. The
landscape preset leans harder on sharpness and exposure, because a landscape usually should
be sharp throughout. The wildlife preset leans harder on relative sharpness within the burst,
because across a burst of a moving animal the real question is which frame caught it best.

One honest caveat: these weights were chosen by judgment, not derived from data. Nobody has
yet fed the program a large set of your own keep and reject decisions and tuned the numbers
to match. Treat the scores as a useful ordering, not as truth.

## Step 11: Three different rankings

The program now produces three separate rankings, and confusing them is easy, so they are
kept strictly apart.

**Burst rank** orders the frames inside one burst. Our photograph places second of nine.
Rank one in each burst is that burst's **winner**.

**Global quality rank** orders every photograph in the entire run against every other. Our
photograph places 340th out of 1,200.

**Selection rank** orders only the burst winners against each other. This is the shortlist:
one frame per burst, sorted best first.

Our heron photo has now reached the end of its journey. It placed second in its burst, which
means it lost to the frame beside it, which means it never reaches the shortlist. Its
neighbor, `DSC_4470.NEF`, was very slightly sharper and goes forward in its place.

Each row also carries a one-line reason in plain words. For our heron it reads something
like `2nd of 9 in burst; scored 3% lower than DSC_4470.NEF, mainly on sharpness`. The
terminal shows the same kind of line beside each shortlisted winner, so you can see why a
frame won without opening a spreadsheet.

Before anything else happens, the program writes every one of these numbers to
`evaluation.csv`, one row per photograph. This happens before it asks you any questions, so
that an interruption cannot throw away work that has already been done.

## Step 12: Your say

You are not obligated to accept any of this.

If you pass a feedback file, your decisions override the program's. Marking a photograph
"keep" forces it into the selection even if it lost its burst, which is exactly what our
heron photo would need. Marking one "reject" removes it even if it won. Forced keeps can
push the selection above the number you asked for, because honoring your explicit choice
matters more than hitting a target count.

The program also asks how many photographs you want. You can answer with a number, with
"all," or with nothing at all if you only want the report.

If you ask for variety, the program does something more interesting than taking the top N.
Using the CLIP descriptions from Step 7, it balances quality against novelty, so that
selecting thirty photographs does not hand you thirty near-identical frames of the same
heron. Each pick weighs how good a photograph is against how different it is from what has
already been chosen.

Whatever the path, the final list is sorted by score. That way the best photograph is always
first, and any star rating derived from position agrees with measured quality.

## Step 13: Leaving with the picks

For the photographs that made it, the program prepares to copy.

It plans the entire operation before touching a single file. It gathers each selected
photograph's whole asset family, checks that no two files would land on the same destination
name, confirms that every source really sits inside the folder you named, and verifies there
is enough free space. Only when the complete plan passes every check does copying begin.

Each file is copied to a temporary name and then renamed into place. That means an
interrupted copy leaves a discarded temporary file rather than a half-written photograph
that looks complete. The program refuses to overwrite anything that already exists.

Folder structure is preserved, so `day-two/DSC_4470.NEF` arrives at
`picks/day-two/DSC_4470.NEF` rather than being dumped into one flat pile.

If you asked for star ratings, they are written into small sidecar files **next to the
copies**, never next to your originals. Your source folder ends the operation exactly as it
began. The top ten percent of the selection get five stars, the next thirty percent get
four, and the rest get three.

## What happened to our photograph

`DSC_4471.NEF` was discovered, grouped with its JPEG, decoded once, measured for sharpness
and exposure, scored by three neural networks, ranked against 1,199 other frames, matched
into a burst of nine, and beaten by a single neighboring frame that was very slightly
sharper.

It was not selected. It was also not deleted, moved, renamed, or altered in any way. It sits
exactly where it always sat. Every number the program calculated about it is recorded in
`evaluation.csv`, so you can sort that file yourself, disagree with the outcome, and put the
photograph back into the selection with a one-line feedback entry.

That is the whole point. The program is fast at the boring part, which is looking at 1,200
frames and noticing that nine of them are the same heron. You remain the one who decides
which heron photograph you actually wanted.

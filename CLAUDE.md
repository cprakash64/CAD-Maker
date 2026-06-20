# CLAUDE.md

## SourceCAD CAD generation rules

This app must generate mechanical CAD from plain English.

Do not implement whole-part prompt-to-template routing as the primary generation path.

Correct pipeline:
plain English → strict CAD feature graph → validation → deterministic CadQuery compiler → STEP/STL export → validation → optional repair.

The LLM may output only structured JSON matching the CAD schema.
The LLM must not output executable Python for production generation.
The CAD worker must never execute arbitrary code from the LLM.

Use reusable parametric primitives:
- box
- plate
- cylinder
- circular flange
- pipe
- boss
- wall
- rib
- gusset
- hole
- rectangular hole pattern
- circular hole pattern
- slot
- V groove
- fillet
- chamfer
- shell
- boolean union/subtract

A “template” may only mean a reusable feature builder, not a saved whole-part model.

The app should preserve user dimensions exactly.
When critical dimensions are missing, ask a clarification question.
When dimensions are clear, generate CAD without asking the user to rewrite the prompt.

Validation is mandatory:
- exported STEP exists
- exported STL exists
- model is not empty
- bounding box is close to expected dimensions
- expected hole counts match plan metadata
- compile errors are surfaced clearly

Regression prompts:
- rectangular mounting plate
- NEMA 17 motor plate
- blind flange
- straight pipe spool
- T pipe fitting
- vise jaw
- U bracket
- bearing block
- L bracket
- hinge bracket
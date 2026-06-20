/** Onboarding example prompts, one per common part family. These are UI
 * examples / eval prompts only — generation composes primitives from the prompt,
 * it does not route to a saved template per example. */
export const ONBOARDING_EXAMPLES: { label: string; prompt: string }[] = [
  {
    label: "Mounting bracket",
    prompt:
      "Mounting bracket 80mm wide, 40mm deep, 5mm thick with two M6 holes and rounded corners.",
  },
  {
    label: "Electronics enclosure",
    prompt:
      "Electronics enclosure 100mm wide, 60mm deep, 40mm tall with 2.5mm walls and a screw-down lid.",
  },
  {
    label: "Drill jig",
    prompt:
      "Drill jig plate 120mm by 80mm, 6mm thick, with 6mm guide holes spaced 25mm and a registration lip.",
  },
  {
    label: "Adapter plate",
    prompt:
      "Adapter plate 100mm square, 6mm thick, with a 30mm center bore and four M6 holes.",
  },
  {
    label: "Pipe clamp",
    prompt: "Pipe clamp for a 25mm pipe, 25mm wide, 6mm thick, with two M6 holes.",
  },
  {
    label: "Spacer / standoff",
    prompt: "M6 cylindrical standoff spacer, 12mm outer diameter, 20mm long.",
  },
  {
    label: "Blind flange",
    prompt:
      "Blind flange, 150mm outer diameter, 18mm thick, with 8 M10 clearance holes on a 120mm PCD and no center bore.",
  },
  {
    label: "Pipe spool",
    prompt:
      "Straight pipe spool 200mm long, 80mm pipe outer diameter, 60mm bore, with circular flanges on both ends. Each flange is 120mm OD, 12mm thick, with 8 M8 holes on a 100mm PCD.",
  },
  {
    label: "U-bracket",
    prompt:
      "U-shaped bracket, 80mm wide, 60mm tall, 6mm thick, with two M6 holes on the base and one 8mm pivot hole through each side wall.",
  },
  {
    label: "Bearing block",
    prompt:
      "Bearing block for a 20mm shaft with a 90mm by 45mm by 12mm base, a raised cylindrical boss 45mm OD and 30mm tall, a 20mm through bore, and four M6 mounting holes.",
  },
];

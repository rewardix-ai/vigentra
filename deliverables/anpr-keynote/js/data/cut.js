/* Scenes left out of the talk (12 Oct: the presentation drilled down to the essentials). Their code,
 * notes and data stay; delete a name here to bring a scene back. tools/storyboard.py and
 * tools/speech_html.py read this list too, so the notes and the speech follow the deck. */
window.K = window.K || {};
K.CUT = new Set([
  "detect", "frames", "gate", "read", "vote",      // the reading in detail: the signal chain shows it
  "field",                                         // the field-test videos: the results show the numbers
  "system",                                        // the simple schematic: the HLD shows the system
  "console",                                       // the old console recording: the screenshots show today's console
  "features-find", "features-act",                 // the feature cards: the screenshots show the features
  "stack", "choices", "dropped",                   // under the hood: for questions, see docs/models.md
  "roadmap",                                       // near-term plans: the 80,000-camera scenes show the future
]);

# Report design system

Use this reference when changing the local HTML report renderer. It adapts the Sana Agents visual language to an offline data report.

## Visual principles

1. Build hierarchy with Paper White, Frost Wash, and Ink Black flat surfaces. Do not add shadows or gradients.
2. Use a 24px radius for cards and panels and a 9999px radius for buttons and tags.
3. Use Ink Black for primary actions on light surfaces. Reserve Electric Lime for low-frequency emphasis on Ink Black.
4. Center the single editorial display headline. Keep report content left aligned.
5. Use only verified local project imagery. Prefer typography and data visualization when no real asset is available.
6. Lead with one compact dark verdict container. Keep generous outer whitespace; it must not become a full-screen dark curtain.
7. Inside the verdict container, answer three fixed questions in order: what the user mainly did, the clearest progress, and the first issue to solve. Give each answer one concise related-signal line.
8. Place up to three prioritized action cards immediately after the verdict. Move activity charts into a collapsed detail section.
9. Use Electric Lime only for low-frequency emphasis. Honor `prefers-reduced-motion` if motion is added later.
10. On wide desktop screens only, allow four low-opacity OpenAI Blossom marks and several independent terminal-route segments along the outer margins. Each segment may use a thin dashed line, small circular nodes, a `>_` prompt, and a rare Electric Lime cursor square; do not use a continuous maze or dense dot grid. Use the Blossom exactly as supplied by OpenAI, without recoloring or redraws; keep every ornament behind content, free of text and controls, and hide it below 1360px or in print. Keep these ornaments as inline SVG and CSS in the renderer so every normal Skill installation reproduces the same background without network assets.

## Design tokens

The CSS tokens are identical to the Chinese reference. Keep the renderer zero-dependency and preserve the existing variables when making visual changes:

```css
:root {
  --color-ink: #0a1217;
  --color-paper: #ffffff;
  --color-frost: #e4eff7;
  --color-stone: #85898b;
  --color-obsidian: #000000;
  --color-lime: #cdfe00;
  --radius-card: 24px;
  --radius-pill: 9999px;
  --spacing-section: 64px;
}
```

The distributed report is a zero-dependency HTML file. Do not add Tailwind or a browser runtime dependency to the auditor.

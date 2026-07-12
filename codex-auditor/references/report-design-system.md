# Report design system

Use this reference when changing the local HTML report renderer. It adapts the Sana Agents style extracted from the referenced Refero Styles page to an offline data report.

## Visual principles

1. Build hierarchy with Paper White, Frost Wash, and Ink Black flat surfaces. Do not add shadows or gradients.
2. Use a 24px radius for cards and panels and a 9999px radius for buttons and tags.
3. Use Ink Black for primary actions on light surfaces. Reserve Electric Lime for low-frequency emphasis on Ink Black.
4. Center the single editorial display headline. Keep report content left aligned.
5. Use only verified local project imagery. Prefer typography and data visualization when no real asset is available.

## Design tokens

```css
:root {
  --color-ink: #0a1217;
  --color-paper: #ffffff;
  --color-frost: #e4eff7;
  --color-stone: #85898b;
  --color-obsidian: #000000;
  --color-lime: #cdfe00;
  --text-caption: 13px;
  --text-body: 16px;
  --text-heading: 20px;
  --text-display: 72px;
  --radius-card: 24px;
  --radius-pill: 9999px;
  --space-1: 8px;
  --space-2: 16px;
  --space-3: 24px;
  --space-4: 32px;
  --space-section: 64px;
  --page-max: 1200px;
  --control-height: 44px;
}
```

## Tailwind v4 theme mapping

```css
@theme {
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

The distributed report is a zero-dependency HTML file, so the renderer emits compiled CSS that consumes the same variables directly. Do not add Tailwind or a browser runtime dependency to the auditor.

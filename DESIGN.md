# 食事筆記 · Design rules

Chosen from the explicit brief, informed by ui-ux-pro-max's Japanese serif/sans pairing and restaurant palette search. Generic funnel/hero and vivid block recommendations were rejected as unrelated to a shared notebook.

- Paper `#f5f0e6`, page `#fffcf5`, ink `#302c26`, secondary ink `#686052`, vermilion `#993e2e`, matcha `#4f6147`, rule `#c6bcab`, control edge `#8c806e`. Text pairs meet AA.
- Display: `"Noto Serif JP","Noto Serif TC","Yu Mincho","Hiragino Mincho ProN",serif`. Body: `"Noto Sans TC","Noto Sans JP","Yu Gothic UI","Microsoft JhengHei",sans-serif`. Installed fonts only; no network requests for typography.
- Shared authority: `static/css/tokens.css`. Type: 12 / 14 / 16 / 20 / 24 / 32 / 48px, fluid title. Spacing: 4 / 8 / 12 / 16 / 24 / 32 / 48px. Radius 2px. Focus: 3px matcha with 3px offset. Motion: 140ms color feedback, disabled under reduced motion.
- Signature: square vermilion 食 stamp, paired fine/double rules and menu-style price columns. No ornamental icons, gradients, floating cards, shadows or slogans. Real restaurant images stay restrained, with reserved aspect boxes.
- Public: searchable ruled menu, full details in a native modal dialog, full-screen mobile sheet. Detail keeps hours and comments intact. Prices never guessed.
- Map: actual My Maps embed beside a linked shop index; a selected shop gets an explicit selected label and links. No fabricated map coordinates.
- Login: quiet single-column form. Admin: same paper and ink, denser list/editor workbench; bulk actions separated below, direction and consequence explicit. Legacy notes read-only; editor payload omits comments so saves preserve the current DB text, including concurrent additions.
- Traditional Chinese and Japanese share `tabelogLanguage` localStorage key; document language and pressed states follow it. User-entered content is never translated or inserted as HTML.
- All controls have labels, keyboard paths and 44px targets. Fixed aspect images, wrapping text, visible status, retry/empty states, no essential animation. At 360px: filters stack, details fill the viewport, admin switches list/editor with a return button.

Public comment metadata shows dates only, with no author names or aliases. Admin comments retain created_by. Generated legacy author headers are redacted only in the public projection.

# Plan B implementation notes — tooling-version deviations

The plan-doc was written assuming Vite 5 / React 18 / Tailwind v3 / Radix-based
shadcn / `@radix-ui/react-toast`. The actual 2026 ecosystem moved on; here are
the intentional substitutions that landed during execution:

| Plan assumed | Actually shipped | Reason |
| --- | --- | --- |
| Vite 5 | **Vite 8** | latest stable; `npm create vite@latest` produces this |
| React 18 | **React 19** | bundled with vite-create's `react-ts` template |
| TypeScript 5 | **TypeScript 6** | bundled with vite-create's template; `baseUrl` is deprecated, use `"ignoreDeprecations": "6.0"` |
| ESLint v8 `.eslintrc.cjs` | **ESLint v9 flat config** (`eslint.config.js`) | Vite scaffold uses flat config; the legacy `.eslintrc.cjs` is silently ignored |
| `@typescript-eslint/parser` + `eslint-plugin` separately | umbrella `typescript-eslint` package | flat-config canonical form |
| Tailwind v3 (`tailwind.config.js` + `postcss.config.js`) | **Tailwind v4** (`@tailwindcss/vite` plugin + CSS-first config) | v3 still works but v4 is the modern path; no `tailwind.config.js` exists |
| `@tailwindcss/typography` JS plugin | `@plugin "@tailwindcss/typography";` in `index.css` | v4 plugin registration syntax |
| shadcn `toast` + `useToast` hook | **shadcn `sonner`** (`toast` from `"sonner"`) | `toast` was removed from shadcn 4.7.0; sonner is the replacement |
| Radix-based shadcn primitives | **base-ui-based shadcn primitives** in some components | `TooltipTrigger` uses `render={<span/>}` instead of Radix's `asChild` |

Other intentional adjustments during execution:
- A `next-themes` `<ThemeProvider>` is mounted at app root because `sonner.tsx`
  calls `useTheme()` from next-themes. The plan's Zustand `theme` store was
  removed in Task 6 to avoid two sources of truth.
- The plan-original `useChatStream` mutated `state.messages[N].run_id`
  directly; a `patchAssistantRunId` store action was added in Task 4 instead.
- `Composer.disabled` is wired to `isStreaming` (any assistant message with
  `status==="streaming"`) to prevent the double-send race that would corrupt
  `patchAssistantRunId`'s "last assistant message with run_id===null" guard.

Anyone reading the plan-doc alongside the diff should consult this file first.

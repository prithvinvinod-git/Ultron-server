<!-- LOVABLE:BEGIN -->
> [!IMPORTANT]
> This project is connected to [Lovable](https://lovable.dev). Avoid rewriting
> published git history — force pushing, or rebasing/amending/squashing commits
> that are already pushed — as it rewrites history on Lovable's side and the
> user will likely lose their project history.
>
> Commits you push to the connected branch sync back to Lovable and show up in
> the editor, so keep the branch in a working state.
<!-- LOVABLE:END -->

## Application architecture
- The index route owns the Ultron command-center experience, with isolated visual and interaction components under `src/components/ultron`; this keeps the core display independent from utility panels.
- All telemetry and desktop tools are client-side demonstration state, not live device monitoring; this UI-only redesign must not imply access to the user's operating system.
- Global semantic tokens and HUD styling live in `src/styles.css` so every panel and control uses one coherent design system.

# Flutter Engineering Guidelines

- These pages describe how to write, structure, and review Dart and Flutter code.
- For every architecture decision, confirm that the user understands and owns the whole architecture.
- For every architecture decision, ALWAYS provide an explanation for the reasons of the decision and confirm with the user.
- Significant decisions must be documented under an [ADR](../dotnet/architecture-principles.md#architecture-decision-records-adrs).
- Route the task to the below pages and read them before any code change.

**Baseline:** Flutter 3.44 (stable) · Dart 3.12 · sound null safety · Material 3 · `flutter_bloc` for state management.

## Contents

| Page | What it covers |
|------|----------------|
| [Dart Best Practices](./dart-best-practices.md) | Language-level guidance: naming, imports, types, null safety, immutability and equality, records, patterns, sealed classes, class modifiers, extensions, collections, errors, async, isolates, documentation, testing. |
| [Flutter Best Practices](./flutter-best-practices.md) | Framework-level guidance: project setup, widget composition, widget lifecycle, `BuildContext`, keys, navigation, theming, localization, networking, platform integration, error handling, testing, security, dependencies. |
| [Flutter Performance](./flutter-performance.md) | Measuring in profile mode, rebuild cost, lists and scrolling, layout and paint cost, images, animations, isolates, startup, app size, memory, and advanced topics (Impeller, custom render objects, platform views, web, FFI, performance tests in CI). |
| [UI/UX Guidelines](./flutter-ui-ux-guidelines.md) | Design system and tokens, platform conventions, responsive and adaptive layout, touch targets, text scaling, colour and contrast, accessibility, loading/empty/error states, forms, motion, navigation, right-to-left and localized content. |
| [BLoC Best Practices](./bloc-best-practices.md) | The BLoC pattern and the `bloc` package: Bloc vs Cubit, designing and splitting blocs, events, states, concurrency, providing and consuming blocs, dependency injection, forms, localization, testing, and how to structure the application, based on the large-scale reference app thesis. |
| [Flutter Architecture](./flutter-architecture.md) | Separation of concerns, the layers and the dependency rule, where state lives, cross-feature communication, feature modules ("micro frontends") in a monorepo, composition root, navigation, error flow, enforcing boundaries, testing per layer. |

## Suggested reading order

1. **New to Dart or Flutter:** Dart → Flutter → BLoC → Architecture.
2. **Starting a new feature or app:** Architecture → BLoC → UI/UX.
3. **Working on jank, slow startup, or a large app binary:** Performance.
4. **Building a screen:** UI/UX → Flutter → Performance.
5. **Reviewing a PR:** go to the *Review checklist* at the end of each page.

## How the pages are organised

Every page follows the same shape as the [.NET guidelines](../dotnet/index.md):

- **Do / Do not** bullets: the rule in one line.
- **Examples**: `❌` marks code to avoid, `✅` marks the preferred alternative.
- **Why** (where the rule does not make the reason clear): the reason for the rule, so that you can decide about edge cases.
- **Review checklist**: a short list to use during code review.

The code samples do not include doc comments, because this keeps them short. Real code must document every public class and member with `///` comments, as [Dart › Documentation comments](./dart-best-practices.md#documentation-comments) describes.

General principles that are not specific to Flutter (SOLID, coupling and cohesion, ADRs) are in the .NET pages. These pages link to them and do not repeat them. For example:

- The *principle* of dependency inversion is in [SOLID](../dotnet/solid-principles.md#dependency-inversion-principle-dip).
- The *Flutter mechanics* of repository injection are in [BLoC › Dependency injection](./bloc-best-practices.md#dependency-injection).
- *How packages connect* is in [Architecture › Composition root](./flutter-architecture.md#composition-root).

## Citing a rule in a review

Headings do not change, so link to them directly:

> See [Flutter › BuildContext](./flutter-best-practices.md#buildcontext): this uses `context` after an `await` without checking `context.mounted`.

- If a guideline is old or wrong, change it in a PR, as you do for code.
- If nobody follows a guideline, correct it or remove it.
- Do not ignore it. Always ask for permission for removing or adding a guideline.

import type { TestingLibraryMatchers } from '@testing-library/jest-dom/matchers';
import 'vitest';

declare module 'vitest' {
  // Vitest 5: custom matchers must augment Matchers<R, T> (not Assertion<T>
  // or jest.Matchers). jest-dom 7 still ships the Vitest 4 Assertion<T> shape.
  // Empty body is the required declaration-merge form; `_T` preserves arity.
  // eslint-disable-next-line @typescript-eslint/no-empty-object-type -- merge-only matcher augmentation
  interface Matchers<R = void, _T = unknown>
    extends TestingLibraryMatchers<any, R> {}
}

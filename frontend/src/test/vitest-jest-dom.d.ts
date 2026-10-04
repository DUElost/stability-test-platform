import type { TestingLibraryMatchers } from '@testing-library/jest-dom/matchers';
import 'vitest';

declare module 'vitest' {
  // Vitest 5: custom matchers must augment Matchers<R, T> (not Assertion<T>
  // or jest.Matchers). jest-dom 7 still ships the Vitest 4 Assertion<T> shape.
  interface Matchers<R = void, T = unknown>
    extends TestingLibraryMatchers<any, R> {}
}

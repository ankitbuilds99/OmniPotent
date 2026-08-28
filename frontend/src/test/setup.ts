import '@testing-library/jest-dom';
import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';

afterEach(() => {
  cleanup();
});

// jsdom implements none of these three, so every one has to be supplied here.
//
// They used to be `vi.fn().mockImplementation(...)`. That looks harmless and is
// not: `vi.restoreAllMocks()` strips the implementation back off a vi.fn, and 21
// of the 25 test files call it in afterEach. So the first test in a file got a
// working IntersectionObserver and every test after it got a constructor
// returning undefined — `observer.observe is not a function`, thrown from inside
// framer-motion, in a test that had nothing to do with mocks.
//
// Nothing asserts on calls to any of them, so they do not need to be spies.
// Plain classes cannot be restored out from under a test.

class NoopObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
  takeRecords(): [] {
    return [];
  }
}

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});

Object.defineProperty(window, 'ResizeObserver', {
  writable: true,
  value: NoopObserver,
});

Object.defineProperty(window, 'IntersectionObserver', {
  writable: true,
  value: NoopObserver,
});

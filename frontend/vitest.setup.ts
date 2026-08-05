import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
import "@testing-library/jest-dom/vitest";

// RTL's auto-cleanup only self-registers when it finds a GLOBAL `afterEach`
// (test.globals isn't set here — tests import from "vitest" explicitly), so
// without this every test file's renders would pile up in the same document.
afterEach(cleanup);

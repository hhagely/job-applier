// adapter-node emits ES modules (`import './shims.js'`) into build/, but no
// package.json of its own. This project's web/package.json has "type":"module",
// so `node build` works in place. The moment build/ is relocated away from this
// tree, though, it loses that marker: the Electron packager copies build/ into the
// app's resources/web/, where the nearest package.json is CommonJS, so Node loads
// handler.js as CJS and throws "Cannot use import statement outside a module" at
// startup. Drop a self-contained marker next to the output so it stays ESM wherever
// it lands. Runs automatically as npm's `postbuild` after `vite build`.
import { writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const buildDir = join(dirname(fileURLToPath(import.meta.url)), '..', 'build');
writeFileSync(join(buildDir, 'package.json'), JSON.stringify({ type: 'module' }) + '\n');

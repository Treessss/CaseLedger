// Safari 13.0/iPadOS 13.3 cannot parse nullish coalescing or optional chaining.
// Audit all emitted JavaScript, including dependencies and preload helpers.
const fs = require('node:fs');
const path = require('node:path');
const acorn = require('acorn');

const directory = path.resolve(__dirname, '../dist');
let checked = 0;
function checkDirectory(root) {
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const file = path.join(root, entry.name);
    if (entry.isDirectory()) checkDirectory(file);
    else if (entry.name.endsWith('.js')) {
      try {
        acorn.parse(fs.readFileSync(file, 'utf8'), { ecmaVersion: 2019, sourceType: 'script' });
        checked += 1;
      } catch (error) {
        throw new Error(`Safari 13 syntax check failed: ${path.relative(directory, file)}: ${error.message}`);
      }
    }
  }
}
checkDirectory(directory);
if (!checked) throw new Error('No built JavaScript found to check');
console.log(`Safari 13 syntax check passed (${checked} JavaScript assets)`);

/**
 * Pixel-compare two screenshot sets. A type-scale refactor that only RENAMES
 * sizes must be pixel-identical; anything that differs is either a real change
 * or a mistake, and either way must be looked at deliberately.
 */
import { readFileSync, readdirSync, existsSync } from 'node:fs';
import { createHash } from 'node:crypto';

const [a, b] = process.argv.slice(2);
if (!a || !b) { console.error('usage: node pixdiff.mjs <dirA> <dirB>'); process.exit(2); }
const hash = f => createHash('sha256').update(readFileSync(f)).digest('hex').slice(0, 12);
const files = readdirSync(a).filter(f => f.endsWith('.png')).sort();
let same = 0; const diff = [];
for (const f of files) {
  const pb = `${b}/${f}`;
  if (!existsSync(pb)) { diff.push(`${f}  (missing in ${b})`); continue; }
  const ha = hash(`${a}/${f}`), hb = hash(pb);
  if (ha === hb) same++; else diff.push(`${f}  ${ha} -> ${hb}`);
}
console.log(`  identical: ${same}/${files.length}`);
if (diff.length) { console.log('  DIFFERENT:'); diff.forEach(d => console.log('    ' + d)); }

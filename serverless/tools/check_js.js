/* Parse the page's JS and confirm every getElementById target exists in the HTML. */
const fs = require('fs');
const vm = require('vm');

const p = 'c:/Users/sidda/Downloads/Smart-Podcast-Finder/static/index.html';
const html = fs.readFileSync(p, 'utf8');

const m = html.match(/<script>([\s\S]*)<\/script>\s*<\/body>/);
if (!m) { console.log('FAIL: could not extract script'); process.exit(1); }
const js = m[1];

try {
  new vm.Script(js);
  console.log('ok    javascript parses');
} catch (e) {
  console.log('FAIL  syntax error:', e.message);
  process.exit(1);
}

const ids = [...js.matchAll(/getElementById\('([^']+)'\)/g)].map(x => x[1]);
const missing = [...new Set(ids)].filter(id => !html.includes(`id="${id}"`));
if (missing.length) {
  console.log('FAIL  referenced but missing from HTML:', missing);
  process.exit(1);
}
console.log(`ok    all ${new Set(ids).size} element ids exist`);

for (const need of ['show-form', 'episode-list', 'askAgent', 'reenableEpisodes',
                    '/api/agent/episodes', 'transcript-body', 'setMinutes',
                    'mins-opt', 'dd-provenance', 'currentMinutes']) {
  if (!js.includes(need) && !html.includes(need)) {
    console.log('FAIL  missing:', need);
    process.exit(1);
  }
}
console.log('ok    episode picker, duration selector and provenance banner wired');
console.log('\nVERDICT: PASS');

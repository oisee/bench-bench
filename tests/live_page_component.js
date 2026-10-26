// Dependency-free DOM fixture for the offline dashboard component's behavior.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.dataset = {}; this.value = ''; this.hidden = false; this.attributes = {}; }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return (this.text || '') + this.children.map(c => c.textContent ?? String(c)).join(''); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.text = ''; this.children = nodes; }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener() {}
  click() { this.onclick(); }
  get selectedOptions() { return this.children.filter(n => n.value === this.value); }
}
const elements = {};
const document = {
  getElementById: id => elements[id] ||= new Element('div'),
  createElement: tag => new Element(tag),
  createTextNode: text => ({textContent: text}),
  addEventListener() {},
};
const context = { document, Node: Element, fixture: input.state, assert, URLSearchParams };
vm.runInNewContext(input.script + `
state=fixture;draw();
assert.equal($('group').children.length,2);
assert.equal($('executor').value,'alpha');
assert.equal($('refs').children.length,8);
assert.equal($('refs').children[0].attributes['aria-current'],'true');
assert.equal($('refs').children[0].children[1].textContent,'2 / 2');
assert.equal($('show-all').hidden,false);
$('show-all').click();assert.equal($('refs').children.length,11);
$('show-all').click();assert.equal($('refs').children.length,8);
const refs=$('tasks').children[0].children[7].children[0];
assert.equal(refs.tagName,'details');assert.ok(!refs.open);
assert.equal(refs.children[0].textContent,'1/2 ✓');
assert.ok(refs.children[0].title.includes('beta: RESOLVED'));
assert.equal(duration(75),'1:15');assert.equal(duration(3600),'60:00');
assert.equal(duration(null),'—');assert.equal(duration(0),'0:00');
$('search').value='no-such-task';drawTasks();
assert.equal($('tasks').textContent,'No matching tasks');
$('search').value='';$('filter').value='RESOLVED';drawTasks();
assert.equal($('tasks').children.length,2);
$('filter').value='FAILED';drawTasks();
assert.equal($('tasks').textContent,'No matching tasks');
`, context);

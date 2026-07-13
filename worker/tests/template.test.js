const test = require('node:test');
const assert = require('node:assert/strict');
const { renderTemplate } = require('../template');

test('renderTemplate fills in placeholders from fields', () => {
  const result = renderTemplate(
    'Hi {{name}}, deadline for {{program}} is soon.',
    { name: 'Aigerim', program: 'Fall Cohort' }
  );
  assert.equal(result, 'Hi Aigerim, deadline for Fall Cohort is soon.');
});

test('renderTemplate throws on missing field', () => {
  assert.throws(() => {
    renderTemplate('Hi {{name}}', {});
  }, /Missing template field: name/);
});

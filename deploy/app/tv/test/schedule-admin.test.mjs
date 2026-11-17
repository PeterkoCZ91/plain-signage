// Item schedule in the admin: pure logic from the schedule:begin/end block (label, building the object, validation).
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const TV = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const src = fs.readFileSync(path.join(TV, 'admin.html'), 'utf8');
const m = src.match(/\/\/ schedule:begin([\s\S]*?)\/\/ schedule:end/);
assert.ok(m, 'block schedule:begin/end not found');
const ctx = vm.createContext({});
vm.runInContext(m[1] + '\nthis.formatSchedule = formatSchedule; this.buildSchedule = buildSchedule; this.planDays = planDays;', ctx);
const { formatSchedule } = ctx;
const buildSchedule = (...a) => JSON.parse(JSON.stringify(ctx.buildSchedule(...a)));  // an object from another realm

const CS = ['Po', 'Út', 'St', 'Čt', 'Pá', 'So', 'Ne'];
const EN = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

test('consecutive days as a range (cs and en)', () => {
  const s = { days: [0, 1, 2, 3, 4], from: '18:00', to: '23:00' };
  assert.equal(formatSchedule(s, CS, 'každý den'), 'Po–Pá 18:00–23:00');
  assert.equal(formatSchedule(s, EN, 'every day'), 'Mon–Fri 18:00–23:00');
});

test('single and non-consecutive days, a pair with a comma, unsorted input', () => {
  assert.equal(formatSchedule({ days: [2], from: '08:00', to: '09:30' }, CS, 'x'), 'St 08:00–09:30');
  assert.equal(formatSchedule({ days: [4, 0, 2], from: '08:00', to: '09:00' }, CS, 'x'), 'Po, St, Pá 08:00–09:00');
  assert.equal(formatSchedule({ days: [0, 1], from: '08:00', to: '09:00' }, CS, 'x'), 'Po, Út 08:00–09:00');
  assert.equal(formatSchedule({ days: [0, 1, 2, 4, 5, 6], from: '08:00', to: '09:00' }, CS, 'x'), 'Po–St, Pá–Ne 08:00–09:00');
});

test('across midnight the time is printed as it is', () => {
  assert.equal(formatSchedule({ from: '22:00', to: '02:00' }, CS, 'každý den'), 'každý den 22:00–02:00');
  assert.ok(formatSchedule({ days: [4, 5], from: '22:00', to: '02:00' }, EN, 'every day').endsWith('22:00–02:00'));
});

test('empty days and all seven days = every day', () => {
  assert.equal(formatSchedule({ days: [], from: '10:00', to: '11:00' }, CS, 'každý den'), 'každý den 10:00–11:00');
  assert.equal(formatSchedule({ days: [0, 1, 2, 3, 4, 5, 6], from: '10:00', to: '11:00' }, EN, 'every day'), 'every day 10:00–11:00');
  assert.equal(formatSchedule(null, CS, 'každý den'), '');
});

test('validation: from == to and missing times are invalid', () => {
  assert.deepEqual(buildSchedule([0], '10:00', '10:00'), { error: 'same' });
  assert.deepEqual(buildSchedule([0], '', '10:00'), { error: 'times' });
  assert.deepEqual(buildSchedule([0], '10:00', ''), { error: 'times' });
  assert.deepEqual(buildSchedule([], '', ''), { error: 'times' });
  assert.deepEqual(buildSchedule([0], '25:00', '10:00'), { error: 'times' });
});

test('output object: days sorted and unique, with no days the days key is missing', () => {
  assert.deepEqual((buildSchedule([4, 0, 0, 2], '18:00', '23:00')),
    { schedule: { days: [0, 2, 4], from: '18:00', to: '23:00' } });
  assert.deepEqual((buildSchedule([], '22:00', '02:00')),
    { schedule: { from: '22:00', to: '02:00' } });
  assert.deepEqual((buildSchedule([9, -1, 'x', 6], '08:00:00', '09:00')),
    { schedule: { days: [6], from: '08:00', to: '09:00' } });
});

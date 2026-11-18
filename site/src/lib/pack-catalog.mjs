import data from '../data/leaderboard.json';
import { parse } from 'yaml';
import { makePackCatalog } from './packs.mjs';

const v2 = Object.values(import.meta.glob('../data/leaderboard-p95-v2.0.0.json', { eager: true, import: 'default' }))[0];
const v1Tasks = import.meta.glob('../../../tasks/**/*.yaml', { eager: true, query: '?raw', import: 'default' });
const v2Tasks = import.meta.glob('../../../packs/p95-v2.0.0/tasks/**/*.yaml', { eager: true, query: '?raw', import: 'default' });
const sources = import.meta.glob('../../../results/published/*/attempts.jsonl', { eager: true, query: '?raw', import: 'default' });
const attempts = Object.values(sources).flatMap(source => source.split('\n').filter(line => line.trim()).map(JSON.parse));
export const packCatalog = makePackCatalog(data, v2, {
  v1: Object.values(v1Tasks).map(source => parse(source)),
  v2: Object.values(v2Tasks).map(source => parse(source)),
}, attempts);

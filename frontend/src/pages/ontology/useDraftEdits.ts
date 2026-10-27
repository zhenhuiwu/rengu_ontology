/**
 * 动作类型 / 事件类型草稿里的增删改（只给 ActionTypes.vue、EventTypes.vue 用）。
 * 草稿来源、视图切换、发起变更仍用公共的 parts/useDraftView；这里只管“草稿里改了什么”：
 *   - section(name, base, keyOf)：一张可编辑的表（参数、提交条件、写回、载荷字段、订阅方……），
 *     行带改动标记 新增 / 修改 / 删除（和已发布版比较）；
 *   - 改动先进“未保存”，保存草稿后计入变更单改动数；有未保存改动时切换左侧对象、切回已发布、离开页面都要确认（useUnsavedGuard）；
 *   - 提交审核后草稿锁定，不能再改。
 * 状态按 变更单 + 对象 保存在模块里，切换页签不丢。
 * 变更单写库（palantir-cr）：传了 writer 且 writer.on() 时，保存草稿 / 提交审核 / 放弃草稿先写库（src/api/action.ts crWriter），
 *   成功后再改本地状态；失败弹错误、本地状态不变（不回退 mock、不走 fallBackIfDown）。writer.on() 为 false 时行为同以前。
 * palantir-r3：写库模式下 ① 改动数读 writer.changes（库里整单的改动数）；② 选中对象在草稿单里、本地还没有它的草稿时，
 *   用 writer.load 读回库里的草稿行叠到 saved（刷新页面后草稿还在）；读失败只提示，不当成“没有草稿”。
 */
import { computed, reactive, watch, type Ref } from 'vue';
import { currentApiContext } from '@/api/http';
import { message } from 'ant-design-vue';
import { useRouter } from 'vue-router';
import type { DraftCR } from '@/mock/releases';
import { useUnsavedGuard } from '@/composables/useUnsavedGuard';
import { useConfirmStore } from '@/stores/confirm';

export type DiffMark = '新增' | '修改' | '删除' | '';
export interface DiffRow<T> { key: string; data: T; base?: T; mark: DiffMark }
interface Row { key: string; data: unknown; deleted?: boolean }

const saved = reactive<Record<string, Row[]>>({});
const working = reactive<Record<string, Row[]>>({});
const submitted = reactive<Record<string, string>>({});
/** 已从库里读回过草稿行的 变更单|对象（每个只读一次；本会话保存 / 放弃后以本地为准） */
const loaded = new Set<string>();

export function resetDraftEdits() {
  for (const state of [saved, working, submitted]) for (const key of Object.keys(state)) delete state[key];
  loaded.clear();
}

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/** 一次保存里某个 section 变了的行（相对上一次保存）；op=revert 表示改回已发布版，要从草稿里去掉 */
export interface SectionChange { key: string; op: 'add' | 'update' | 'delete' | 'revert'; before?: unknown; after?: unknown }
export interface DraftWriter {
  on: () => boolean;
  save: (crId: string, changes: { section: string; rows: SectionChange[] }[]) => Promise<void>;
  submit: (crId: string) => Promise<void>;
  discard: (crId: string) => Promise<void>;
  /** 库里整单的改动数（没有记录时 undefined，退回前端计数） */
  changes?: (crId: string) => number | undefined;
  /** 读回本对象在该单里的全部草稿行（刷新后恢复） */
  load?: (crId: string, targetId: string) => Promise<{ section: string; key: string; op: string; after?: unknown }[]>;
  /** 错误提示文字（带错误码、422 的字段） */
  errText?: (e: unknown) => string;
}

export interface DraftEditsOptions {
  draft: Ref<DraftCR | undefined>;
  isDraft: Ref<boolean>;
  editTip: Ref<string>;
  targetId: () => string;
  targetName: () => string;
  reviewer?: string;
  writer?: DraftWriter;
  /** 已发布版数据已就绪（真接口快照载入完）；读回草稿行要等它，否则叠在 mock 上 */
  ready?: () => boolean;
}

export function useDraftEdits(o: DraftEditsOptions) {
  const confirm = useConfirmStore();
  const router = useRouter();
  const workspacePrefix = `${currentApiContext().ontologyId}|`;
  const prefix = () => `${workspacePrefix}${o.draft.value?.id ?? '-'}|${o.targetId()}|`;
  const locked = computed(() => !!o.draft.value && !!submitted[workspacePrefix + o.draft.value.id]);
  const canEdit = computed(() => o.isDraft.value && !locked.value);
  const editTip = computed(() => (o.isDraft.value && locked.value ? `${o.draft.value!.id} 已提交审核，驳回后才能继续修改` : o.editTip.value));
  const dirty = computed(() => Object.keys(working).some((k) => k.startsWith(prefix())));
  const sections: { name: string; marks: (src: 'saved' | 'all') => number; changes: () => SectionChange[]; hydrate: (items: { key: string; op: string; after?: unknown }[]) => void }[] = [];
  const writing = () => !!o.writer?.on();
  const errText = (e: unknown) => o.writer?.errText?.(e) ?? (e instanceof Error ? e.message : String(e));
  let busy = false;

  function section<T>(name: string, base: () => T[], keyOf: (t: T) => string) {
    const key = () => prefix() + name;
    const baseRows = (): Row[] => base().map((d) => ({ key: keyOf(d), data: d }));
    const current = (): Row[] => working[key()] ?? saved[key()] ?? baseRows();
    const diff = (src: Row[]): DiffRow<T>[] => {
      const b = new Map(baseRows().map((r) => [r.key, r.data as T]));
      return src.map((r) => ({
        key: r.key, data: r.data as T, base: b.get(r.key),
        mark: r.deleted ? '删除' : !b.has(r.key) ? '新增' : same(b.get(r.key), r.data) ? '' : '修改',
      }));
    };
    const rows = computed<DiffRow<T>[]>(() => (o.isDraft.value ? diff(current()) : base().map((d) => ({ key: keyOf(d), data: d, mark: '' as const }))));
    const edit = (fn: (rs: Row[]) => void) => {
      if (!canEdit.value) return;
      const rs = current().map((r) => ({ ...r }));
      fn(rs);
      working[key()] = rs;
    };
    /** 未保存的行里，相对上一次保存变了的（按与已发布版比较给 op） */
    const changes = (): SectionChange[] => {
      const w = working[key()];
      if (!w) return [];
      const prev = new Map((saved[key()] ?? baseRows()).map((r) => [r.key, r]));
      const now = new Map(w.map((r) => [r.key, r]));
      const b = new Map(baseRows().map((r) => [r.key, r.data]));
      const out: SectionChange[] = [];
      for (const k of new Set([...prev.keys(), ...now.keys()])) {
        const r = now.get(k);
        if (same(prev.get(k), r)) continue;
        if (!r) { out.push({ key: k, op: 'revert' }); continue; }
        if (r.deleted) out.push({ key: k, op: 'delete', before: b.get(k) });
        else if (!b.has(k)) out.push({ key: k, op: 'add', after: r.data });
        else if (same(b.get(k), r.data)) out.push({ key: k, op: 'revert' });
        else out.push({ key: k, op: 'update', before: b.get(k), after: r.data });
      }
      return out;
    };
    /** 库里的草稿行叠到已发布版上，作为“已保存”（本地已有草稿时不覆盖） */
    const hydrate = (items: { key: string; op: string; after?: unknown }[]) => {
      if (!items.length || saved[key()] || working[key()]) return;
      const rs = baseRows();
      for (const it of items) {
        const i = rs.findIndex((r) => r.key === it.key);
        if (it.op === 'delete') { if (i >= 0) rs[i] = { ...rs[i], deleted: true }; }
        else if (i >= 0) rs[i] = { key: it.key, data: it.after };
        else rs.push({ key: it.key, data: it.after });
      }
      saved[key()] = rs;
    };
    sections.push({ name, changes, hydrate, marks: (src) => diff(src === 'saved' ? saved[key()] ?? baseRows() : current()).filter((r) => r.mark).length });
    return {
      rows,
      /** 键已存在（未删除）时返回 false */
      add(d: T) {
        if (current().some((r) => r.key === keyOf(d) && !r.deleted)) return false;
        edit((rs) => { const i = rs.findIndex((r) => r.key === keyOf(d)); if (i >= 0) rs[i] = { key: keyOf(d), data: d }; else rs.push({ key: keyOf(d), data: d }); });
        return true;
      },
      update(k: string, d: T) { edit((rs) => { const r = rs.find((x) => x.key === k); if (r) r.data = d; }); },
      remove(k: string) {
        const isBase = baseRows().some((r) => r.key === k);
        edit((rs) => { const i = rs.findIndex((x) => x.key === k); if (i < 0) return; if (isBase) rs[i].deleted = true; else rs.splice(i, 1); });
      },
      restore(k: string) {
        const b = baseRows().find((r) => r.key === k);
        edit((rs) => { const r = rs.find((x) => x.key === k); if (r && b) { r.deleted = false; r.data = b.data; } });
      },
    };
  }

  /** 已保存进草稿的改动数 / 含未保存的改动数 */
  const savedChanges = computed(() => sections.reduce((s, x) => s + x.marks('saved'), 0));
  const allChanges = computed(() => sections.reduce((s, x) => s + x.marks('all'), 0));
  /** 给 DraftBanner 用：改动数 = 变更单原有改动 + 本对象已保存的改动 */
  /** 整单改动数：写库模式且库里有数时以库为准（已含本对象保存进库的行），否则 = 单子原有改动 + 本对象已保存的改动 */
  const dbChanges = () => (writing() && o.draft.value ? o.writer!.changes?.(o.draft.value.id) : undefined);
  const crChangeCount = () => dbChanges() ?? (o.draft.value ? o.draft.value.changes + savedChanges.value : 0);
  const bannerDraft = computed<DraftCR | undefined>(() => o.draft.value && { ...o.draft.value, changes: crChangeCount(), ...(dbChanges() === undefined && savedChanges.value ? { updatedAt: '2026-10-25 17:10' } : {}) });

  /* 刷新后恢复：选中对象在草稿单里时读回库里的草稿行（每个 单号|对象 一次） */
  watch(() => [o.draft.value?.id, o.targetId(), o.ready?.() ?? true] as const, async ([crId, tid, rd]) => {
    if (!crId || !rd || !writing() || !o.writer?.load) return;
    const p = `${workspacePrefix}${crId}|${tid}|`;
    if (loaded.has(p)) return;
    loaded.add(p);
    try {
      const items = await o.writer.load(crId, tid);
      if (prefix() !== p) { loaded.delete(p); return; } // 读回来时已切到别的对象：下次选中再读
      for (const x of sections) x.hydrate(items.filter((it) => it.section === x.name));
    } catch (e) { loaded.delete(p); message.error(`读取草稿改动失败：${errText(e)}`); }
  }, { immediate: true });

  async function save() {
    const d = o.draft.value;
    if (writing() && d) {
      if (busy) return;
      const changes = sections.map((x) => ({ section: x.name, rows: x.changes() })).filter((x) => x.rows.length);
      busy = true;
      try { if (changes.length) await o.writer!.save(d.id, changes); } catch (e) { message.error(`保存草稿失败：${errText(e)}`); return; } finally { busy = false; }
    }
    Object.keys(working).filter((k) => k.startsWith(prefix())).forEach((k) => { saved[k] = working[k]; delete working[k]; });
    message.success('已保存到草稿');
  }
  function revert() { Object.keys(working).filter((k) => k.startsWith(prefix())).forEach((k) => delete working[k]); }
  const guardRaw = useUnsavedGuard(() => dirty.value);
  /** 有未保存改动时先确认；确认放弃后丢掉未保存改动再执行 fn */
  const guard = (fn: () => void) => guardRaw(() => { revert(); fn(); });

  async function submit() {
    const d = o.draft.value;
    if (!d) return;
    if (locked.value) { message.info(`${d.id} 已提交审核`); return; }
    const ok = await confirm.ask({
      title: '提交审核', target: `${d.id} ${d.title}`, okText: '提交',
      blocked: dirty.value ? '本对象还有未保存的修改，请先保存草稿或撤销修改后再提交' : undefined,
      fields: [{ label: '本对象', value: o.targetName() }, { label: '本对象改动', value: `${savedChanges.value} 处` }, { label: '变更单改动', value: `${crChangeCount()} 处` }, ...(o.reviewer ? [{ label: '审核人', value: o.reviewer }] : [])],
      impact: '提交后草稿锁定，审核人驳回后才能继续修改；审核通过后随下一次整版发布生效，发布前问数和规则仍用已发布版本。',
    });
    if (!ok) return;
    if (writing()) { try { await o.writer!.submit(d.id); } catch (e) { message.error(`提交审核失败：${errText(e)}`); return; } }
    submitted[workspacePrefix + d.id] = '2026-10-25 17:10'; message.success(`${d.id} 已提交评审`); router.push({ path: '/build/ontology/releases', query: { cr: d.id } });
  }
  async function discard(after: () => void) {
    const d = o.draft.value;
    if (!d) return;
    const ok = await confirm.ask({ title: '放弃草稿', target: `${d.id} · ${o.targetName()}`, okText: '放弃', danger: true,
      impact: '丢弃本对象在草稿里的全部改动（含已保存的），已发布版本不受影响。' });
    if (!ok) return;
    if (writing()) { try { await o.writer!.discard(d.id); } catch (e) { message.error(`放弃草稿失败：${errText(e)}`); return; } }
    Object.keys({ ...saved, ...working }).filter((k) => k.startsWith(prefix())).forEach((k) => { delete saved[k]; delete working[k]; });
    after();
  }
  return { section, canEdit, editTip, locked, dirty, savedChanges, allChanges, bannerDraft, save, revert, guard, submit, discard };
}

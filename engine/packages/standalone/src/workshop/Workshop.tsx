import {
  For,
  Show,
  createEffect,
  createMemo,
  createSignal,
  on,
} from "solid-js";
import {
  CARD_BY_ID,
  CATALOG,
  CATALOG_VERSION,
  CHARACTER_BY_ID,
  VANILLA_PRESET,
  clonePreset,
  countPresetChanges,
  diceMapToRecord,
  diceRecordToMap,
  normalizeDiceRecord,
  resolveCostDice,
  sameDiceRecord,
  setCostTotal,
  isEmptyPreset,
  type CardOverride,
  type CharacterOverride,
  type DiceRecord,
  type RulesPreset,
  type SkillOverride,
} from "@gi-tcg/local-rules";
import {
  activePreset,
  deletePreset,
  exportPresetsJson,
  importPresetsJson,
  newPresetId,
  renamePreset,
  savePreset,
  selectPreset,
  store,
  uniquePresetName,
} from "./storage";
import { CostEditor, DiceChips } from "./CostEditor";
import "./workshop.css";

export interface WorkshopProps {
  onBack: () => void;
  /** 用当前预设开始对局（App 会切到本地模拟） */
  onStart: () => void;
}

type Tab = "characters" | "cards" | "skills";

const ELEMENT_LABEL: Record<string, string> = {
  cryo: "冰",
  hydro: "水",
  pyro: "火",
  electro: "雷",
  anemo: "风",
  geo: "岩",
  dendro: "草",
};
const WEAPON_LABEL: Record<string, string> = {
  sword: "单手剑",
  claymore: "双手剑",
  pole: "长柄武器",
  catalyst: "法器",
  bow: "弓",
};
const TYPE_LABEL: Record<string, string> = {
  eventCard: "事件牌",
  equipment: "装备牌",
  support: "支援牌",
  weapon: "武器",
  artifact: "圣遗物",
  talent: "天赋",
  food: "料理",
  place: "场地",
  ally: "伙伴",
  item: "道具",
};
const SKILL_TYPE_LABEL: Record<string, string> = {
  normal: "普通攻击",
  elemental: "元素战技",
  burst: "元素爆发",
  passive: "被动",
  playCard: "打出",
  technique: "特技",
};
const DAMAGE_TYPE_LABEL: Record<string, string> = {
  Physical: "物理",
  Cryo: "冰",
  Hydro: "水",
  Pyro: "火",
  Electro: "雷",
  Anemo: "风",
  Geo: "岩",
  Dendro: "草",
  Piercing: "穿透",
};

function elementOf(tags: string[]): string {
  for (const t of tags) if (ELEMENT_LABEL[t]) return t;
  return "";
}
function weaponOf(tags: string[]): string {
  for (const t of tags) if (WEAPON_LABEL[t]) return t;
  return "";
}
function tagsText(tags: string[]): string {
  return tags
    .map((t) => TYPE_LABEL[t] ?? ELEMENT_LABEL[t] ?? WEAPON_LABEL[t] ?? t)
    .join(" · ");
}
function damageText(damages: { type: string; value: number }[]): string {
  if (!damages.length) return "—";
  return damages
    .map((d) => `${d.value} ${DAMAGE_TYPE_LABEL[d.type] ?? d.type}`)
    .join(" / ");
}

function sanitizeNumber(raw: string, fallback: number, min: number, max: number): number | null {
  if (raw.trim() === "") return null;
  const n = Number(raw);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, Math.round(n)));
}

export function Workshop(props: WorkshopProps) {
  const [tab, setTab] = createSignal<Tab>("characters");
  const [search, setSearch] = createSignal("");
  const [selected, setSelected] = createSignal<Set<number>>(new Set());
  const [batchValue, setBatchValue] = createSignal("100");
  const [draft, setDraft] = createSignal<RulesPreset>(clonePreset(activePreset()));
  const [dirty, setDirty] = createSignal(false);
  const [costEditor, setCostEditor] = createSignal<{
    kind: "card" | "skill";
    id: number;
    title: string;
    defaultDice: DiceRecord;
    value: DiceRecord;
  } | null>(null);
  const [toast, setToast] = createSignal("");
  let toastTimer: number | undefined;
  let importInput!: HTMLInputElement;

  createEffect(
    on(
      () => store().activeId,
      () => {
        setDraft(clonePreset(activePreset()));
        setDirty(false);
        setSelected(new Set<number>());
      },
      { defer: true },
    ),
  );

  function notify(message: string) {
    setToast(message);
    if (toastTimer !== undefined) clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => setToast(""), 2600);
  }

  /** 修改草稿；如果当前是“原版”，自动派生一个“自定义”预设。 */
  function mutateDraft(fn: (d: RulesPreset) => void): void {
    let d = draft();
    if (d.builtin) {
      d = {
        ...clonePreset(d),
        id: newPresetId(),
        name: uniquePresetName("自定义"),
        builtin: false,
      };
    } else {
      d = clonePreset(d);
    }
    fn(d);
    setDraft(d);
    setDirty(true);
  }

  function commit(): RulesPreset {
    const d = draft();
    if (isEmptyPreset(d)) {
      if (d.builtin) {
        selectPreset(VANILLA_PRESET.id);
        setDirty(false);
        return VANILLA_PRESET;
      }
      // 自定义预设被清空：保存成“空预设”，而不是丢掉用户建的这个预设
      const saved = savePreset({ ...d, name: d.name.trim() || "未命名预设" });
      setDraft(clonePreset(saved));
      setDirty(false);
      return saved;
    }
    const saved = savePreset({ ...d, name: d.name.trim() || "未命名预设" });
    setDraft(clonePreset(saved));
    setDirty(false);
    return saved;
  }

  function switchPreset(id: string): boolean {
    if (id === draft().id) return true;
    if (dirty() && !confirm("当前预设还有未保存的修改，确定要切换吗？")) return false;
    selectPreset(id);
    return true;
  }

  function handleNew() {
    if (dirty() && !confirm("当前预设还有未保存的修改，确定要新建吗？")) return;
    const preset: RulesPreset = {
      id: newPresetId(),
      name: uniquePresetName("自定义"),
    };
    savePreset(preset);
    notify("已新建预设");
  }

  function handleDuplicate() {
    const copy: RulesPreset = {
      ...clonePreset(draft()),
      id: newPresetId(),
      name: uniquePresetName(`${draft().name} 副本`),
      builtin: false,
    };
    savePreset(copy);
    notify("已复制预设");
  }

  function handleRename() {
    if (draft().builtin) {
      notify("内置“原版”预设不能重命名");
      return;
    }
    const name = prompt("预设名称", draft().name)?.trim();
    if (!name) return;
    renamePreset(draft().id, name);
    setDraft({ ...draft(), name });
  }

  function handleDelete() {
    if (draft().builtin) {
      notify("内置“原版”预设不能删除");
      return;
    }
    if (!confirm(`删除预设“${draft().name}”？`)) return;
    deletePreset(draft().id);
    notify("已删除");
  }

  function handleReset() {
    if (!confirm("把当前预设的所有数值恢复为原版？")) return;
    mutateDraft((d) => {
      delete d.characters;
      delete d.cards;
      delete d.skills;
    });
    notify("已恢复为原版数值（记得保存）");
  }

  function handleExport() {
    const blob = new Blob([exportPresetsJson()], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "gi-tcg-presets.json";
    a.click();
    URL.revokeObjectURL(url);
  }

  async function handleImportFile(file: File) {
    const text = await file.text();
    const result = importPresetsJson(text);
    if (result.error) notify(`导入失败：${result.error}`);
    else notify(`已导入 ${result.imported} 个预设`);
  }

  // ---------- 数值读写 ----------
  function charValue(id: number, key: "hp" | "energy"): number {
    const d = draft().characters?.[String(id)];
    const def = CHARACTER_BY_ID.get(id)!;
    if (key === "hp") return d?.hp ?? def.hp;
    return d?.energy ?? def.energy;
  }

  function defaultCharValue(id: number, key: "hp" | "energy"): number {
    const def = CHARACTER_BY_ID.get(id)!;
    return key === "hp" ? def.hp : def.energy;
  }

  function setCharValue(id: number, key: "hp" | "energy", raw: string) {
    const def = defaultCharValue(id, key);
    const max = key === "hp" ? 9999 : 99;
    const min = key === "hp" ? 1 : 0;
    const value = sanitizeNumber(raw, def, min, max);
    mutateDraft((d) => {
      d.characters ??= {};
      const cur: CharacterOverride = { ...(d.characters[String(id)] ?? {}) };
      if (value === null || value === def) delete cur[key];
      else cur[key] = value;
      if (Object.keys(cur).length) d.characters[String(id)] = cur;
      else delete d.characters[String(id)];
      if (Object.keys(d.characters).length === 0) delete d.characters;
    });
  }

  function cardValue(id: number, key: "cost" | "damageBonus"): number | null {
    const d = draft().cards?.[String(id)];
    const def = CARD_BY_ID.get(id)!;
    if (key === "cost") return d?.cost ?? def.cost;
    return d?.damageBonus ?? (def.damageBonus.length && new Set(def.damageBonus).size === 1 ? def.damageBonus[0] : null);
  }

  function setCardValue(id: number, key: "cost" | "damageBonus", raw: string) {
    const def = CARD_BY_ID.get(id)!;
    const defaultValue = key === "cost" ? def.cost : def.damageBonus.length === 1 ? def.damageBonus[0] : null;
    const max = key === "cost" ? 99 : 9999;
    const value = sanitizeNumber(raw, defaultValue ?? 0, 0, max);
    mutateDraft((d) => {
      d.cards ??= {};
      const cur: CardOverride = { ...(d.cards[String(id)] ?? {}) };
      if (value === null || value === defaultValue) delete cur[key];
      else cur[key] = value;
      if (Object.keys(cur).length) d.cards[String(id)] = cur;
      else delete d.cards[String(id)];
      if (Object.keys(d.cards).length === 0) delete d.cards;
    });
  }

  function skillValue(id: number, def: number): number {
    return draft().skills?.[String(id)]?.damage ?? def;
  }

  function setSkillValue(id: number, def: number, raw: string) {
    const value = sanitizeNumber(raw, def, 0, 9999);
    mutateDraft((d) => {
      d.skills ??= {};
      const cur: SkillOverride = { ...(d.skills[String(id)] ?? {}) };
      if (value === null || value === def) delete cur.damage;
      else cur.damage = value;
      if (Object.keys(cur).length) d.skills[String(id)] = cur;
      else delete d.skills[String(id)];
      if (Object.keys(d.skills).length === 0) delete d.skills;
    });
  }

  // ---------- 骰子消耗 ----------
  function cardDefaultDice(id: number): DiceRecord {
    return normalizeDiceRecord(CARD_BY_ID.get(id)?.costDice);
  }

  function resolveCardDice(d: RulesPreset, id: number): DiceRecord {
    const def = cardDefaultDice(id);
    const override = d.cards?.[String(id)];
    return resolveCostDice(def, override);
  }

  function effectiveCardDice(id: number): DiceRecord {
    return resolveCardDice(draft(), id);
  }

  function setCardDice(id: number, dice: DiceRecord) {
    const def = cardDefaultDice(id);
    mutateDraft((d) => {
      d.cards ??= {};
      const cur: CardOverride = { ...(d.cards[String(id)] ?? {}) };
      if (sameDiceRecord(dice, def)) delete cur.dice;
      else cur.dice = normalizeDiceRecord(dice);
      delete cur.cost;
      if (Object.keys(cur).length) d.cards[String(id)] = cur;
      else delete d.cards[String(id)];
      if (Object.keys(d.cards).length === 0) delete d.cards;
    });
  }

  function setCardCostTotal(id: number, total: number) {
    const next = setCostTotal(diceRecordToMap(effectiveCardDice(id)), total);
    setCardDice(id, diceMapToRecord(next));
  }

  function skillCatalogEntry(id: number) {
    return allSkills().find((s) => s.id === id);
  }

  function skillDefaultDice(id: number): DiceRecord {
    return normalizeDiceRecord(skillCatalogEntry(id)?.costDice);
  }

  function resolveSkillDice(d: RulesPreset, id: number): DiceRecord {
    const def = skillDefaultDice(id);
    const override = d.skills?.[String(id)];
    return resolveCostDice(def, override);
  }

  function effectiveSkillDice(id: number): DiceRecord {
    return resolveSkillDice(draft(), id);
  }

  function setSkillDice(id: number, dice: DiceRecord) {
    const def = skillDefaultDice(id);
    mutateDraft((d) => {
      d.skills ??= {};
      const cur: SkillOverride = { ...(d.skills[String(id)] ?? {}) };
      if (sameDiceRecord(dice, def)) delete cur.dice;
      else cur.dice = normalizeDiceRecord(dice);
      delete cur.cost;
      if (Object.keys(cur).length) d.skills[String(id)] = cur;
      else delete d.skills[String(id)];
      if (Object.keys(d.skills).length === 0) delete d.skills;
    });
  }

  function setSkillCostTotal(id: number, total: number) {
    const next = setCostTotal(diceRecordToMap(effectiveSkillDice(id)), total);
    setSkillDice(id, diceMapToRecord(next));
  }

  function openCardCost(id: number) {
    const card = CARD_BY_ID.get(id);
    setCostEditor({
      kind: "card",
      id,
      title: `${card?.name ?? `#${id}`} · 打出费用`,
      defaultDice: cardDefaultDice(id),
      value: effectiveCardDice(id),
    });
  }

  function openSkillCost(id: number) {
    const skill = skillCatalogEntry(id);
    setCostEditor({
      kind: "skill",
      id,
      title: `${skill?.name ?? `#${id}`} · 技能消耗`,
      defaultDice: skillDefaultDice(id),
      value: effectiveSkillDice(id),
    });
  }

  function applyCostEditor(dice: DiceRecord) {
    const editor = costEditor();
    if (!editor) return;
    if (editor.kind === "card") setCardDice(editor.id, dice);
    else setSkillDice(editor.id, dice);
  }

  function resetCostEditor() {
    const editor = costEditor();
    if (!editor) return;
    if (editor.kind === "card") setCardDice(editor.id, editor.defaultDice);
    else setSkillDice(editor.id, editor.defaultDice);
  }

  // ---------- 筛选 ----------
  const allSkills = createMemo(() =>
    CATALOG.characters.flatMap((c) =>
      c.skills.map((s) => ({
        ...s,
        characterId: c.id,
        characterName: c.name,
        element: elementOf(c.tags),
      })),
    ),
  );

  const query = () => search().trim().toLowerCase();
  const matches = (name: string, id: number, tags: string[] = []) => {
    const q = query();
    if (!q) return true;
    return (
      name.toLowerCase().includes(q) ||
      String(id).includes(q) ||
      tags.some((t) => (TYPE_LABEL[t] ?? ELEMENT_LABEL[t] ?? WEAPON_LABEL[t] ?? t).includes(q) || t.includes(q))
    );
  };

  const filteredCharacters = createMemo(() =>
    CATALOG.characters.filter((c) => matches(c.name, c.id, c.tags)),
  );
  const filteredCards = createMemo(() =>
    CATALOG.cards.filter((c) => matches(c.name, c.id, [...c.tags, c.type])),
  );
  const filteredSkills = createMemo(() =>
    allSkills().filter((s) => matches(s.name, s.id, [s.characterName, s.type])),
  );

  const currentIds = createMemo(() => {
    switch (tab()) {
      case "characters":
        return filteredCharacters().map((c) => c.id);
      case "cards":
        return filteredCards().map((c) => c.id);
      case "skills":
        return filteredSkills().map((s) => s.id);
    }
  });

  const selectedCount = () => selected().size;
  const allSelected = () =>
    currentIds().length > 0 && currentIds().every((id) => selected().has(id));

  function toggle(id: number) {
    const next = new Set(selected());
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  }

  function toggleAll() {
    if (allSelected()) setSelected(new Set<number>());
    else setSelected(new Set(currentIds()));
  }

  function switchTab(next: Tab) {
    setTab(next);
    setSelected(new Set<number>());
  }

  function batchApply(kind: "hp" | "energy" | "cost" | "damageBonus" | "damage") {
    const ids = [...selected()];
    if (!ids.length) {
      notify("先勾选要批量修改的条目");
      return;
    }
    const n = Number(batchValue());
    if (!Number.isFinite(n)) {
      notify("批量数值不合法");
      return;
    }
    let changed = 0;
    mutateDraft((d) => {
      if (kind === "hp" || kind === "energy") {
        d.characters ??= {};
        for (const id of ids) {
          const cur: CharacterOverride = { ...(d.characters[String(id)] ?? {}) };
          cur[kind] = Math.min(kind === "hp" ? 9999 : 99, Math.max(kind === "hp" ? 1 : 0, Math.round(n)));
          d.characters[String(id)] = cur;
          changed++;
        }
      } else if (kind === "damageBonus") {
        d.cards ??= {};
        for (const id of ids) {
          const def = CARD_BY_ID.get(id);
          if (!def || def.damageBonus.length === 0) continue;
          const cur: CardOverride = { ...(d.cards[String(id)] ?? {}) };
          cur.damageBonus = Math.min(9999, Math.max(0, Math.round(n)));
          d.cards[String(id)] = cur;
          changed++;
        }
      } else if (kind === "cost") {
        const total = Math.min(99, Math.max(0, Math.round(n)));
        if (tab() === "skills") {
          d.skills ??= {};
          for (const id of ids) {
            const skill = skillCatalogEntry(id);
            if (!skill) continue;
            const cur: SkillOverride = { ...(d.skills[String(id)] ?? {}) };
            const base =
              cur.dice !== undefined
                ? cur.dice
                : typeof cur.cost === "number"
                  ? resolveCostDice(skill.costDice, { cost: cur.cost })
                  : skill.costDice;
            const dice = diceMapToRecord(setCostTotal(diceRecordToMap(base), total));
            delete cur.cost;
            if (sameDiceRecord(dice, skill.costDice)) delete cur.dice;
            else cur.dice = dice;
            if (Object.keys(cur).length) d.skills[String(id)] = cur;
            else delete d.skills[String(id)];
            changed++;
          }
          if (Object.keys(d.skills).length === 0) delete d.skills;
        } else {
          d.cards ??= {};
          for (const id of ids) {
            const def = CARD_BY_ID.get(id);
            if (!def) continue;
            const cur: CardOverride = { ...(d.cards[String(id)] ?? {}) };
            const base =
              cur.dice !== undefined
                ? cur.dice
                : typeof cur.cost === "number"
                  ? resolveCostDice(def.costDice, { cost: cur.cost })
                  : def.costDice;
            const dice = diceMapToRecord(setCostTotal(diceRecordToMap(base), total));
            delete cur.cost;
            if (sameDiceRecord(dice, def.costDice)) delete cur.dice;
            else cur.dice = dice;
            if (Object.keys(cur).length) d.cards[String(id)] = cur;
            else delete d.cards[String(id)];
            changed++;
          }
          if (Object.keys(d.cards).length === 0) delete d.cards;
        }
      } else {
        d.skills ??= {};
        for (const id of ids) {
          const skill = allSkills().find((s) => s.id === id);
          if (!skill || skill.damage.length !== 1) continue;
          const cur: SkillOverride = { ...(d.skills[String(id)] ?? {}) };
          cur.damage = Math.min(9999, Math.max(0, Math.round(n)));
          d.skills[String(id)] = cur;
          changed++;
        }
      }
    });
    notify(`已修改 ${changed} 项`);
  }

  function applyAllWeapons() {
    let changed = 0;
    mutateDraft((d) => {
      d.cards ??= {};
      for (const card of CATALOG.cards) {
        if (!card.tags.includes("weapon") || card.damageBonus.length === 0) continue;
        d.cards[String(card.id)] = {
          ...(d.cards[String(card.id)] ?? {}),
          damageBonus: 100,
        };
        changed++;
      }
    });
    notify(`已把 ${changed} 张武器的伤害加成设为 100（记得保存）`);
  }

  function modifiedRow(id: number): boolean {
    if (tab() === "characters") return !!draft().characters?.[String(id)];
    if (tab() === "cards") return !!draft().cards?.[String(id)];
    return !!draft().skills?.[String(id)];
  }

  const changeCount = createMemo(() => countPresetChanges(draft()));

  return (
    <div class="ws">
      <header class="ws-header">
        <div class="ws-title-row">
          <div>
            <h1>数值工坊</h1>
            <div class="ws-sub">
              数据版本 <b>{CATALOG_VERSION}</b> · {CATALOG.counts.characters} 名角色 ·{" "}
              {CATALOG.counts.cards} 张行动牌 · 只影响启用了本预设的对局
            </div>
          </div>
          <div class="ws-header-actions">
            <button class="ws-btn" onClick={() => { if (!dirty() || confirm("还有未保存的修改，确定返回首页吗？")) props.onBack(); }}>
              返回首页
            </button>
            <button class="ws-btn ws-primary" onClick={() => { commit(); props.onStart(); }}>
              用此预设开始对局
            </button>
          </div>
        </div>

        <div class="ws-preset-bar">
          <label>预设</label>
          <select
            class="ws-select"
            value={store().activeId}
            onChange={(e) => {
              if (!switchPreset(e.currentTarget.value)) {
                e.currentTarget.value = store().activeId;
              }
            }}
          >
            <For each={store().presets}>
              {(p) => (
                <option value={p.id}>
                  {p.name}
                  {p.builtin ? "（内置）" : `（${countPresetChanges(p)} 项改动）`}
                </option>
              )}
            </For>
          </select>
          <button class="ws-btn" onClick={handleNew}>新建</button>
          <button class="ws-btn" onClick={handleDuplicate}>复制</button>
          <button class="ws-btn" onClick={handleRename} disabled={draft().builtin}>重命名</button>
          <button class="ws-btn ws-danger" onClick={handleDelete} disabled={draft().builtin}>删除</button>
          <span class="ws-spacer" />
          <button class="ws-btn" onClick={handleExport}>导出</button>
          <button class="ws-btn" onClick={() => importInput.click()}>导入</button>
          <input
            ref={importInput!}
            type="file"
            accept="application/json,.json"
            style={{ display: "none" }}
            onChange={(e) => {
              const file = e.currentTarget.files?.[0];
              if (file) void handleImportFile(file);
              e.currentTarget.value = "";
            }}
          />
        </div>

        <div class="ws-status-row">
          <span classList={{ "ws-dirty": dirty() }}>
            {dirty() ? "● 有未保存的修改" : "已保存"} · 当前改动 {changeCount()} 项
          </span>
          <Show when={draft().builtin}>
            <span class="ws-hint">正在查看内置“原版”；直接改数值会自动生成一个“自定义”预设。</span>
          </Show>
          <span class="ws-spacer" />
          <button class="ws-btn" onClick={() => { commit(); notify("已保存"); }}>保存预设</button>
          <button class="ws-btn ws-warn" onClick={handleReset}>恢复原版数值</button>
        </div>
      </header>

      <div class="ws-toolbar">
        <div class="ws-tabs">
          <button classList={{ active: tab() === "characters" }} onClick={() => switchTab("characters")}>
            角色 <small>{filteredCharacters().length}</small>
          </button>
          <button classList={{ active: tab() === "cards" }} onClick={() => switchTab("cards")}>
            行动牌 <small>{filteredCards().length}</small>
          </button>
          <button classList={{ active: tab() === "skills" }} onClick={() => switchTab("skills")}>
            技能 <small>{filteredSkills().length}</small>
          </button>
        </div>
        <input
          class="ws-search"
          type="search"
          placeholder="搜索名称 / ID / 标签，例如：武器、凯亚、311501"
          value={search()}
          onInput={(e) => setSearch(e.currentTarget.value)}
        />
        <button class="ws-btn" onClick={toggleAll}>{allSelected() ? "取消全选" : "全选筛选结果"}</button>
        <button class="ws-btn" onClick={applyAllWeapons}>示例：所有武器 +100</button>
      </div>

      <div class="ws-batch">
        <span>批量修改（对勾选条目）</span>
        <input
          class="ws-input"
          type="number"
          min="0"
          max="9999"
          value={batchValue()}
          onInput={(e) => setBatchValue(e.currentTarget.value)}
        />
        <Show when={tab() === "characters"}>
          <button class="ws-btn" onClick={() => batchApply("hp")}>设为生命</button>
          <button class="ws-btn" onClick={() => batchApply("energy")}>设为充能</button>
        </Show>
        <Show when={tab() === "cards"}>
          <button class="ws-btn" onClick={() => batchApply("cost")}>设为费用</button>
          <button class="ws-btn" onClick={() => batchApply("damageBonus")}>设为伤害加成</button>
        </Show>
        <Show when={tab() === "skills"}>
          <button class="ws-btn" onClick={() => batchApply("cost")}>设为费用</button>
          <button class="ws-btn" onClick={() => batchApply("damage")}>设为基础伤害</button>
          <span class="ws-hint">基础伤害只对“单一基础伤害”的技能生效</span>
        </Show>
        <span class="ws-spacer" />
        <span>已选 {selectedCount()} 项</span>
      </div>

      <div class="ws-table-wrap">
        <Show when={tab() === "characters"}>
          <table class="ws-table">
            <thead>
              <tr>
                <th class="ws-check" />
                <th>角色</th>
                <th>元素 / 武器</th>
                <th class="ws-num">生命值</th>
                <th class="ws-num">最大充能</th>
                <th>技能（基础伤害）</th>
              </tr>
            </thead>
            <tbody>
              <For each={filteredCharacters()}>
                {(c) => (
                  <tr classList={{ modified: modifiedRow(c.id), checked: selected().has(c.id) }}>
                    <td class="ws-check">
                      <input type="checkbox" checked={selected().has(c.id)} onChange={() => toggle(c.id)} />
                    </td>
                    <td class="ws-name">
                      {c.name} <span class="ws-id">#{c.id}</span>
                    </td>
                    <td>
                      <span class={`ws-el el-${elementOf(c.tags) || "none"}`}>
                        {ELEMENT_LABEL[elementOf(c.tags)] ?? "—"}
                      </span>
                      <span class="ws-tag">{WEAPON_LABEL[weaponOf(c.tags)] ?? ""}</span>
                    </td>
                    <td class="ws-num">
                      <input
                        class="ws-input"
                        type="number"
                        min="1"
                        max="9999"
                        value={charValue(c.id, "hp")}
                        onChange={(e) => setCharValue(c.id, "hp", e.currentTarget.value)}
                      />
                    </td>
                    <td class="ws-num">
                      <input
                        class="ws-input"
                        type="number"
                        min="0"
                        max="99"
                        value={charValue(c.id, "energy")}
                        onChange={(e) => setCharValue(c.id, "energy", e.currentTarget.value)}
                      />
                    </td>
                    <td class="ws-skills">
                      <For each={c.skills}>
                        {(s) => (
                          <span class="ws-chip" classList={{ "ws-chip-dmg": s.damage.length === 1 }}>
                            {s.name}
                            <i>{damageText(s.damage)}</i>
                          </span>
                        )}
                      </For>
                    </td>
                  </tr>
                )}
              </For>
            </tbody>
          </table>
        </Show>

        <Show when={tab() === "cards"}>
          <table class="ws-table">
            <thead>
              <tr>
                <th class="ws-check" />
                <th>卡牌</th>
                <th>类型</th>
                <th class="ws-num">打出费用（点击编辑）</th>
                <th class="ws-num">伤害加成</th>
                <th>标签</th>
              </tr>
            </thead>
            <tbody>
              <For each={filteredCards()}>
                {(c) => (
                  <tr classList={{ modified: modifiedRow(c.id), checked: selected().has(c.id) }}>
                    <td class="ws-check">
                      <input type="checkbox" checked={selected().has(c.id)} onChange={() => toggle(c.id)} />
                    </td>
                    <td class="ws-name">
                      {c.name} <span class="ws-id">#{c.id}</span>
                    </td>
                    <td>{TYPE_LABEL[c.type] ?? c.type}</td>
                    <td class="ws-num">
                      <button
                        class="ws-cost-btn"
                        title="点击编辑骰子类型和数量"
                        onClick={() => openCardCost(c.id)}
                      >
                        <DiceChips dice={effectiveCardDice(c.id)} />
                        <span class="ws-cost-edit">编辑</span>
                      </button>
                    </td>
                    <td class="ws-num">
                      <Show
                        when={c.damageBonus.length > 0}
                        fallback={<span class="ws-muted">—</span>}
                      >
                        <input
                          class="ws-input"
                          type="number"
                          min="0"
                          max="9999"
                          value={draft().cards?.[String(c.id)]?.damageBonus ?? c.damageBonus[0]}
                          placeholder={c.damageBonus.join("/")}
                          onChange={(e) => setCardValue(c.id, "damageBonus", e.currentTarget.value)}
                        />
                      </Show>
                    </td>
                    <td class="ws-tags">{tagsText(c.tags)}</td>
                  </tr>
                )}
              </For>
            </tbody>
          </table>
        </Show>

        <Show when={tab() === "skills"}>
          <table class="ws-table">
            <thead>
              <tr>
                <th class="ws-check" />
                <th>技能</th>
                <th>角色</th>
                <th>类型</th>
                <th class="ws-num">基础伤害</th>
                <th class="ws-num">消耗骰子</th>
              </tr>
            </thead>
            <tbody>
              <For each={filteredSkills()}>
                {(s) => (
                  <tr classList={{ modified: modifiedRow(s.id), checked: selected().has(s.id) }}>
                    <td class="ws-check">
                      <input type="checkbox" checked={selected().has(s.id)} onChange={() => toggle(s.id)} />
                    </td>
                    <td class="ws-name">
                      {s.name} <span class="ws-id">#{s.id}</span>
                    </td>
                    <td>{s.characterName}</td>
                    <td>{SKILL_TYPE_LABEL[s.type] ?? s.type}</td>
                    <td class="ws-num">
                      <Show
                        when={s.damage.length === 1}
                        fallback={<span class="ws-muted">{damageText(s.damage)}（多段/复杂，只读）</span>}
                      >
                        <input
                          class="ws-input"
                          type="number"
                          min="0"
                          max="9999"
                          value={skillValue(s.id, s.damage[0].value)}
                          onChange={(e) => setSkillValue(s.id, s.damage[0].value, e.currentTarget.value)}
                        />
                        <span class="ws-muted">{s.damage[0].type}</span>
                      </Show>
                    </td>
                    <td class="ws-num">
                      <button
                        class="ws-cost-btn"
                        title="点击编辑骰子类型和数量"
                        onClick={() => openSkillCost(s.id)}
                      >
                        <DiceChips dice={effectiveSkillDice(s.id)} />
                        <span class="ws-cost-edit">编辑</span>
                      </button>
                    </td>
                  </tr>
                )}
              </For>
            </tbody>
          </table>
        </Show>
      </div>

      <Show when={costEditor()}>
        {(editor) => (
          <CostEditor
            title={editor().title}
            defaultDice={editor().defaultDice}
            value={editor().value}
            onApply={applyCostEditor}
            onReset={resetCostEditor}
            onClose={() => setCostEditor(null)}
          />
        )}
      </Show>

      <Show when={toast()}>
        <div class="ws-toast">{toast()}</div>
      </Show>
    </div>
  );
}

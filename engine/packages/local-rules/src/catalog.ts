/**
 * 由 `local/gen-catalog.mjs` 生成的卡牌数值目录。
 * 不要手改 catalog.json；上游更新后重新运行生成器即可。
 */
import raw from "./catalog.json";

export interface CatalogDamage {
  type: string;
  value: number;
}

export interface CatalogSkill {
  id: number;
  name: string;
  type: string;
  damage: CatalogDamage[];
  cost: number;
  /** 骰子消耗：键是 DiceType 数字 */
  costDice: Record<string, number>;
}

export interface CatalogCharacter {
  id: number;
  name: string;
  tags: string[];
  hp: number;
  energy: number;
  skills: CatalogSkill[];
}

export interface CatalogCard {
  id: number;
  name: string;
  type: string;
  tags: string[];
  cost: number;
  /** 骰子消耗：键是 DiceType 数字 */
  costDice: Record<string, number>;
  /** 该卡牌触发的 `increaseDamage(N)` 字面量（通常武器/圣遗物为 [1]） */
  damageBonus: number[];
}

export interface CatalogToken {
  id: number;
  name: string;
  type: string;
  tags: string[];
}

export interface CatalogSkillInfo {
  id: number;
  name: string;
  type: string;
}

export interface Catalog {
  version: string;
  generatedAt: string;
  counts: {
    characters: number;
    cards: number;
    tokens?: number;
    skills?: number;
  };
  characters: CatalogCharacter[];
  cards: CatalogCard[];
  tokens: CatalogToken[];
  skills: CatalogSkillInfo[];
}

export const CATALOG = raw as unknown as Catalog;
export const CATALOG_VERSION: string = CATALOG.version;

export const CHARACTER_BY_ID = new Map(CATALOG.characters.map((c) => [c.id, c]));
export const CARD_BY_ID = new Map(CATALOG.cards.map((c) => [c.id, c]));
export const TOKEN_BY_ID = new Map(CATALOG.tokens.map((c) => [c.id, c]));
export const SKILL_BY_ID = new Map(CATALOG.skills.map((s) => [s.id, s]));

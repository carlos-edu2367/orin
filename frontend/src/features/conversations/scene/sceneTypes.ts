/**
 * Contract of the Cena (pixel-art view of a conversation).
 *
 * `foldScene(events, { running })` (sceneReducer.ts) projects the activity events into a `SceneState`;
 * `SceneCanvas` draws it and `SceneHud` shows the same state as text. Nothing here talks to the API.
 */

/** Where a character stands. Positions live in `sceneStations.ts` (`STATION_LAYOUT`). */
export type SceneStationId =
  | 'terminal' // run_command, powershell, verify_project, shell tasks
  | 'files' // read_file, list_files, glob_files, search_files, search_code, view_file, transcribe_pdf
  | 'writing' // write_file, edit_file, notebook_edit
  | 'browser' // fetch_url, browser tools, preview_*
  | 'planning' // write_contract, todo_write, task_*, plan mode, exit_plan_mode
  | 'meeting' // delegation, ask_agent(s), send_message, run_subagent
  | 'extensions' // MCP, plugins, skills
  | 'generic' // unknown tool
  | 'lounge' // idle, turn finished

export type ScenePose =
  | 'idle'
  | 'walk'
  | 'work' // station-specific work loop (typing, leafing through files...)
  | 'think'
  | 'raise_hand' // waiting for the user (permission / question), holds a "?" sign
  | 'scared' // tool or delegation failure
  | 'wait' // waiting on a subagent (arms crossed)
  | 'deliver' // handing over / returning a paper
  | 'sit' // resting on the lounge sofa

export type SceneAgentStatus =
  | 'working'
  | 'thinking'
  | 'waiting_agent'
  | 'waiting_user'
  | 'queued'
  | 'stopped'
  | 'failed'
  | 'completed'
  | 'resting'

export interface SceneBubble {
  kind: 'thought' | 'speech' | 'error'
  /** Sanitized text cut to ~60 chars (head with trailing "…" for speech/error; tail with leading "…" for thought). */
  text: string
  /** Complete sanitized text of the bubble (kept for the detail view). */
  fullText?: string
  /** Event that produced it, so the canvas can restart its pop-in animation once. */
  eventId: string
}

export interface SceneActivity {
  id: string
  label: string
  detail?: string
  timestamp?: string
}

export interface SceneCustomCharacterPoses {
  idle: string
  think?: string
  work?: string
  wait?: string
  scared?: string
}

export interface SceneCustomCharacter {
  id: string
  name: string
  poses: SceneCustomCharacterPoses
}

export interface SceneAgent {
  /** The raw agentId of the events (main agent normalized to `MAIN_AGENT_ID`). */
  id: string
  isMain: boolean
  name: string
  subagentType?: string
  /** Resolved or event-provided character display name shown on top of the technical tag. */
  characterName?: string
  /** Custom pixel-art character sprites when bound or matched by name. */
  customCharacter?: SceneCustomCharacter
  /** Agent that started this one; `MAIN_AGENT_ID` for agents the Orin started; null for the Orin. */
  parentId: string | null
  /** 0 for the Orin, 1 for an agent the Orin started, 2 for one that agent started... */
  depth: number
  station: SceneStationId
  pose: ScenePose
  status: SceneAgentStatus
  /** pt-BR text of `status` (state never shown by colour/animation alone). */
  statusLabel: string
  task: string
  currentTool: string | null
  lastThought: string
  lastSpeech: string
  bubble: SceneBubble | null
  activities: SceneActivity[]
  durationLabel: string
}

export type SceneTransferKind = 'delegation' | 'return' | 'message' | 'failed_delegation'

/** A paper/envelope travelling between two characters. Keyed by the event id so it plays once. */
export interface SceneTransfer {
  id: string
  kind: SceneTransferKind
  fromId: string
  toId: string
  label?: string
}

export type SceneEffectKind = 'spark' | 'check' | 'poof'

/** One-shot effect at a station / character. Keyed by the event id. */
export interface SceneEffect {
  id: string
  kind: SceneEffectKind
  agentId: string
  station: SceneStationId
}

export type ScenePendingKind = 'permission' | 'question' | 'plan' | 'mcp' | 'plugin'

/** Something the user must answer, anchored to the character that asked. Phase 2 renders the real cards. */
export interface ScenePending {
  id: string
  agentId: string
  kind: ScenePendingKind
  eventId: string
  summary: string
  requestId?: string
}

export interface SceneState {
  turnId: string | null
  /** A turn is running: when false every agent rests and no transfer/effect is kept (no replay). */
  live: boolean
  /** No turn running, but the last one ended asking the user something: the asker keeps holding the sign. */
  waitingUser: boolean
  lightsOn: boolean
  /** Last turn ended in failure/cancel: the canvas blinks the lights once (ids present on mount are not replayed). */
  alarmEventId: string | null
  /** Orin first, then the delegation tree depth-first (`orderAgentTree`). */
  agents: SceneAgent[]
  transfers: SceneTransfer[]
  effects: SceneEffect[]
  pending: ScenePending[]
}

export const MAIN_AGENT_ID = 'agent:main'

import { beforeEach, describe, expect, it } from 'vitest'
import {
  SCENE_SOUND_STORAGE_KEY,
  playSceneSound,
  readSceneSoundPreference,
  resetSceneAudioForTests,
  writeSceneSoundPreference,
} from '../sceneAudio'

describe('sceneAudio', () => {
  beforeEach(() => {
    window.localStorage.clear()
    resetSceneAudioForTests()
  })

  it('defaults sound preference to false', () => {
    expect(readSceneSoundPreference()).toBe(false)
  })

  it('persists and reads back sound preference in localStorage', () => {
    writeSceneSoundPreference(true)
    expect(window.localStorage.getItem(SCENE_SOUND_STORAGE_KEY)).toBe('true')
    expect(readSceneSoundPreference()).toBe(true)

    writeSceneSoundPreference(false)
    expect(window.localStorage.getItem(SCENE_SOUND_STORAGE_KEY)).toBe('false')
    expect(readSceneSoundPreference()).toBe(false)
  })

  it('never throws when playing cues without AudioContext in jsdom', () => {
    expect(() => playSceneSound('step')).not.toThrow()
    expect(() => playSceneSound('type')).not.toThrow()
    expect(() => playSceneSound('ding')).not.toThrow()
    expect(() => playSceneSound('bonk')).not.toThrow()
    expect(() => playSceneSound('envelope')).not.toThrow()
  })
})

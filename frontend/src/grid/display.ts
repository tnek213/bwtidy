import { useDisplay } from '../api/settings.ts'

// Off until the setting has loaded (or when it cannot be loaded), so the Vault grid shows the tool note.
export function useHideToolNote(): boolean {
  return useDisplay().data?.hideToolNote ?? false
}

// 0 (Rare fields off) until the setting has loaded, or when it cannot be loaded.
export function useRareFieldsMax(): number {
  return useDisplay().data?.rareFieldsMax ?? 0
}

import { create } from 'zustand';

export type SettingsStore = {
  email: string | undefined;
  setEmail: (email: string) => void;
  clearEmail: () => void;

  color: string;
  setColor: (color: string) => void;
}

export const useSettingsStore = create<SettingsStore>((set) => ({
    email: undefined,
    setEmail: (email: string) => set({ email }),
    clearEmail: () => set({ email: undefined }),

    color: 'bg-gray-500',
    setColor: (color: string) => set({ color }),
}));
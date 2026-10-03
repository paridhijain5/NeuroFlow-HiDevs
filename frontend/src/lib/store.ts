import { create } from "zustand";

interface PlaygroundState {
  query: string;
  compare: boolean;
  pipelineA: string;
  pipelineB: string;
  setQuery: (q: string) => void;
  setCompare: (c: boolean) => void;
  setPipelineA: (id: string) => void;
  setPipelineB: (id: string) => void;
}

export const usePlayground = create<PlaygroundState>((set) => ({
  query: "",
  compare: false,
  pipelineA: "",
  pipelineB: "",
  setQuery: (query) => set({ query }),
  setCompare: (compare) => set({ compare }),
  setPipelineA: (pipelineA) => set({ pipelineA }),
  setPipelineB: (pipelineB) => set({ pipelineB }),
}));

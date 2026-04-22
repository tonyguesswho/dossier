// cn() helper for shadcn/ui components — merges Tailwind utility classes
// with conflict resolution. Created by shadcn convention; lives at the path
// declared by components.json aliases.utils = "@/lib/utils".
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

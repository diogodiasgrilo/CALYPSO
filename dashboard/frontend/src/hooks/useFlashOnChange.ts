/** Returns "up" | "down" | null CSS class for green/red flash on value change. */

import { useEffect, useRef, useState } from "react";

export function useFlashOnChange(value: number): "up" | "down" | null {
  const prevRef = useRef(value);
  const [flash, setFlash] = useState<"up" | "down" | null>(null);

  useEffect(() => {
    if (value > prevRef.current) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- the flash IS a transient side effect of a value change, cleared by a timer 600ms later; it cannot be derived during render
      setFlash("up");
    } else if (value < prevRef.current) {
      setFlash("down");
    }
    prevRef.current = value;

    const timer = setTimeout(() => setFlash(null), 600);
    return () => clearTimeout(timer);
  }, [value]);

  return flash;
}

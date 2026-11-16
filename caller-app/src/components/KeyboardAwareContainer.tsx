import React, {
  createContext,
  forwardRef,
  useCallback,
  useContext,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from 'react';
import {
  Keyboard,
  Platform,
  ScrollView,
  type ScrollViewProps,
  StyleSheet,
  TextInput,
  View,
} from 'react-native';

type Measurable = {
  measureInWindow: (
    callback: (x: number, y: number, width: number, height: number) => void
  ) => void;
};

const FocusContext = createContext({
  reveal: (_target?: Measurable | null) => {},
  availableHeight: 320,
});

export const useKeyboardField = () => useContext(FocusContext);

export interface KeyboardAwareContainerProps extends ScrollViewProps {
  extraScrollHeight?: number;
}

export interface KeyboardAwareContainerRef {
  scrollTo: (options: { x?: number; y?: number; animated?: boolean }) => void;
  scrollToEnd: (options?: { animated?: boolean }) => void;
  getScrollView: () => ScrollView | null;
}

export const KeyboardAwareContainer = forwardRef<
  KeyboardAwareContainerRef,
  KeyboardAwareContainerProps
>(function KeyboardAwareContainer(
  {
    children,
    extraScrollHeight = 14,
    contentContainerStyle,
    style,
    onScroll,
    onLayout,
    onContentSizeChange,
    ...props
  },
  ref
) {
  const scroll = useRef<ScrollView>(null);
  const viewport = useRef<View>(null);
  const focused = useRef<Measurable | null>(null);
  const offset = useRef(0);
  const keyboardTop = useRef<number | null>(null);
  const frame = useRef<number | null>(null);
  const [overlap, setOverlap] = useState(0);
  const [availableHeight, setAvailableHeight] = useState(320);

  const reveal = useCallback(
    (target?: Measurable | null) => {
      if (target !== undefined) focused.current = target;
      if (frame.current !== null) cancelAnimationFrame(frame.current);
      frame.current = requestAnimationFrame(() => {
        frame.current = null;
        viewport.current?.measureInWindow((_x, y, _w, height) => {
          const bottom = Math.min(y + height, keyboardTop.current ?? y + height);
          setOverlap(Math.max(0, y + height - bottom));
          setAvailableHeight(Math.max(0, bottom - y));
          if (keyboardTop.current === null) return;
          const input = focused.current || TextInput.State.currentlyFocusedInput();
          input?.measureInWindow((_ix: number, iy: number, _iw: number, ih: number) => {
            const delta =
              iy + ih > bottom - extraScrollHeight
                ? iy + ih - bottom + extraScrollHeight
                : iy < y + extraScrollHeight
                  ? iy - y - extraScrollHeight
                  : 0;
            if (Math.abs(delta) > 1) {
              scroll.current?.scrollTo({
                y: Math.max(0, offset.current + delta),
                animated: true,
              });
            }
          });
        });
      });
    },
    [extraScrollHeight]
  );

  useImperativeHandle(ref, () => ({
    scrollTo: (options) => scroll.current?.scrollTo(options),
    scrollToEnd: (options) => scroll.current?.scrollToEnd(options),
    getScrollView: () => scroll.current,
  }));

  useEffect(() => {
    const show = Keyboard.addListener(
      Platform.OS === 'ios' ? 'keyboardWillChangeFrame' : 'keyboardDidShow',
      (event) => {
        keyboardTop.current = event.endCoordinates.screenY;
        reveal();
      }
    );
    const hide = Keyboard.addListener(
      Platform.OS === 'ios' ? 'keyboardWillHide' : 'keyboardDidHide',
      () => {
        keyboardTop.current = null;
        focused.current = null;
        setOverlap(0);
        reveal();
      }
    );
    return () => {
      show.remove();
      hide.remove();
      if (frame.current !== null) cancelAnimationFrame(frame.current);
    };
  }, [reveal]);

  const base = StyleSheet.flatten(contentContainerStyle) || {};
  const basePaddingBottom =
    typeof base.paddingBottom === 'number'
      ? base.paddingBottom
      : typeof base.padding === 'number'
        ? base.padding
        : 0;

  return (
    <FocusContext.Provider value={{ reveal, availableHeight }}>
      <View ref={viewport} style={styles.viewport} onLayout={() => reveal()}>
        <ScrollView
          {...props}
          ref={scroll}
          style={[styles.scroll, style]}
          contentContainerStyle={[
            contentContainerStyle,
            { paddingBottom: basePaddingBottom + overlap },
          ]}
          keyboardShouldPersistTaps="handled"
          keyboardDismissMode={Platform.OS === 'ios' ? 'interactive' : 'on-drag'}
          automaticallyAdjustKeyboardInsets={false}
          scrollEventThrottle={16}
          onScroll={(event) => {
            offset.current = event.nativeEvent.contentOffset.y;
            onScroll?.(event);
          }}
          onLayout={(event) => {
            onLayout?.(event);
            reveal();
          }}
          onContentSizeChange={(w, h) => {
            onContentSizeChange?.(w, h);
            reveal();
          }}
        >
          {children}
        </ScrollView>
      </View>
    </FocusContext.Provider>
  );
});

const styles = StyleSheet.create({
  viewport: {
    flex: 1,
  },
  scroll: {
    flex: 1,
  },
});

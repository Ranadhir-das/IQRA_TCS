import React from 'react';
import { Redirect, Tabs } from 'expo-router';
import { StyleSheet, Text, View } from 'react-native';
import { useAppTheme } from '../../context/AppThemeContext.tsx';
import { useAuth } from '../../context/AuthContext.tsx';

function TabGlyph({ label, focused, color }: { label: string; focused: boolean; color: string }) {
  return (
    <View style={[styles.glyphBox, focused && { borderColor: color }]}>
      <Text style={[styles.glyphText, { color }]}>{label}</Text>
    </View>
  );
}

export default function TabsLayout() {
  const { colors } = useAppTheme();
  const { status, user } = useAuth();

  if (status !== 'authenticated' || !user || user.role !== 'EMPLOYEE' || !user.is_active) {
    return <Redirect href="/login" />;
  }

  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: colors.primary,
        tabBarInactiveTintColor: colors.muted,
        tabBarStyle: {
          backgroundColor: colors.surface,
          borderTopColor: colors.border,
          borderTopWidth: 1,
          height: 64,
          paddingTop: 6,
          paddingBottom: 8,
        },
        tabBarLabelStyle: {
          fontSize: 11.5,
          fontWeight: '600',
        },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: 'Home',
          tabBarIcon: ({ color, focused }) => (
            <TabGlyph label="H" focused={focused} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="leads"
        options={{
          title: 'Leads',
          tabBarIcon: ({ color, focused }) => (
            <TabGlyph label="L" focused={focused} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="follow-ups"
        options={{
          title: 'Follow-Ups',
          tabBarIcon: ({ color, focused }) => (
            <TabGlyph label="F" focused={focused} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="history"
        options={{
          title: 'Calls',
          tabBarIcon: ({ color, focused }) => (
            <TabGlyph label="C" focused={focused} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="account"
        options={{
          title: 'Account',
          tabBarIcon: ({ color, focused }) => (
            <TabGlyph label="A" focused={focused} color={color} />
          ),
        }}
      />
    </Tabs>
  );
}

const styles = StyleSheet.create({
  glyphBox: {
    width: 24,
    height: 24,
    borderRadius: 7,
    borderWidth: 1.5,
    borderColor: 'transparent',
    alignItems: 'center',
    justifyContent: 'center',
  },
  glyphText: {
    fontSize: 12.5,
    fontWeight: '800',
  },
});

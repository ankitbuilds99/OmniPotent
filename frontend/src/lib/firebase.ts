import { FirebaseApp, FirebaseOptions, getApp, getApps, initializeApp } from 'firebase/app'
import { Auth, getAuth } from 'firebase/auth'

const firebaseConfig: FirebaseOptions = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
  storageBucket: import.meta.env.VITE_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID,
  appId: import.meta.env.VITE_FIREBASE_APP_ID,
  measurementId: import.meta.env.VITE_FIREBASE_MEASUREMENT_ID,
}

const requiredConfig: Array<keyof FirebaseOptions> = [
  'apiKey',
  'authDomain',
  'projectId',
  'appId',
]

export const isFirebaseConfigured = requiredConfig.every((key) => Boolean(firebaseConfig[key]))

export const firebaseApp: FirebaseApp | null = isFirebaseConfigured
  ? getApps().length > 0
    ? getApp()
    : initializeApp(firebaseConfig)
  : null

export const firebaseAuth: Auth | null = firebaseApp ? getAuth(firebaseApp) : null

import React from 'react'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

globalThis.React = React
afterEach(cleanup)

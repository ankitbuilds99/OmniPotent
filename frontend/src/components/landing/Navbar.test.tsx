// The landing page's links *into* the API — the navbar's "API" button and the
// Documentation section's /docs and /redoc buttons — were the last two modules
// still writing `?? "http://localhost:8000"` inline, the pattern config.ts was
// created to delete (see api.test.ts for the fetch-side equivalents).
//
// They are hrefs rather than fetches, so none of those assertions covered them,
// and a broken one fails silently: the button opens a tab that cannot connect,
// to a port on the *visitor's* machine. Both read the shared base now.

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { MemoryRouter } from 'react-router-dom'

import { API_BASE_URL } from '../../config'
import { Navbar } from './Navbar'

describe('landing navbar', () => {
  it('builds the API docs link from the shared base, not an inline literal', () => {
    render(
      <MemoryRouter initialEntries={['/marketing']}>
        <Navbar onLaunch={() => {}} />
      </MemoryRouter>
    )

    const apiLink = screen.getAllByRole('link', { name: /API/ })[0]
    expect(apiLink).toHaveAttribute('href', `${API_BASE_URL}/docs`)
  })
})

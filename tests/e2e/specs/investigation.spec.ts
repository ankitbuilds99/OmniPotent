import { test, expect } from '@playwright/test';
import { InvestigationPage } from '../pages/investigation.page';

test.describe('Investigation Flow', () => {
  let investigationPage: InvestigationPage;

  test.beforeEach(async ({ page }) => {
    investigationPage = new InvestigationPage(page);
    await investigationPage.goto();
  });

  test('should load the main page successfully', async ({ page }) => {
    await expect(page).toHaveTitle(/DevOps Copilot/);
    await expect(investigationPage.questionInput).toBeVisible();
    await expect(investigationPage.submitButton).toBeVisible();
  });

  test('should start an investigation with a question', async () => {
    await investigationPage.startInvestigation(
      'Why is the checkout API throwing 500s?'
    );
    await investigationPage.waitForToolCall('datadog_logs', 60000);
    await expect(investigationPage.investigationPanel).toContainText('Gathering evidence');
  });

  test('should display tool calls as they happen', async () => {
    await investigationPage.startInvestigation(
      'What caused the latency spike at 2pm?'
    );
    await investigationPage.waitForToolCall('datadog_metrics');
    const toolCount = await investigationPage.getToolCallCount();
    expect(toolCount).toBeGreaterThan(0);
  });

  test('should show hypotheses before root cause', async () => {
    await investigationPage.startInvestigation(
      'Investigate the memory leak in user service'
    );
    await investigationPage.waitForHypothesis();
    const hypothesisCount = await investigationPage.hypothesisList.locator('li').count();
    expect(hypothesisCount).toBeGreaterThanOrEqual(1);
  });

  test('should require approval for write operations', async () => {
    await investigationPage.startInvestigation(
      'Fix the null pointer in billing service and create a PR'
    );
    await investigationPage.waitForRootCause();
    await investigationPage.fixPrButton.click();
    await expect(investigationPage.approvalModal).toBeVisible();
    await investigationPage.approveAction();
  });

  test('should complete full investigation flow', async () => {
    await investigationPage.startInvestigation(
      'Why did payment service fail at 3pm yesterday?'
    );
    await investigationPage.waitForRootCause(180000);
    const rootCause = await investigationPage.getRootCauseText();
    expect(rootCause.length).toBeGreaterThan(50);
    await investigationPage.expectNoErrors();
  });
});

test.describe('Error Handling', () => {
  test('should show error for empty question', async ({ page }) => {
    const investigationPage = new InvestigationPage(page);
    await investigationPage.goto();
    await investigationPage.submitButton.click();
    await expect(investigationPage.errorToast).toBeVisible();
  });

  test('should handle API timeout gracefully', async ({ page }) => {
    await page.route('**/api/v1/investigate', (route) => {
      setTimeout(() => route.abort('timedout'), 100);
    });
    const investigationPage = new InvestigationPage(page);
    await investigationPage.goto();
    await investigationPage.startInvestigation('Test timeout handling');
    await expect(investigationPage.errorToast).toBeVisible({ timeout: 10000 });
  });

  test('should recover from transient errors', async ({ page }) => {
    let requestCount = 0;
    await page.route('**/api/v1/investigate', (route) => {
      requestCount++;
      if (requestCount < 3) {
        route.fulfill({ status: 503, body: 'Service Unavailable' });
      } else {
        route.continue();
      }
    });
    const investigationPage = new InvestigationPage(page);
    await investigationPage.goto();
    await investigationPage.startInvestigation('Test retry handling');
    await investigationPage.waitForLoad();
    expect(requestCount).toBeGreaterThanOrEqual(3);
  });
});

test.describe('Accessibility', () => {
  test('should have proper ARIA labels', async ({ page }) => {
    const investigationPage = new InvestigationPage(page);
    await investigationPage.goto();
    await expect(investigationPage.questionInput).toHaveAttribute('aria-label');
    await expect(investigationPage.submitButton).toHaveAttribute('aria-label');
  });

  test('should be keyboard navigable', async ({ page }) => {
    const investigationPage = new InvestigationPage(page);
    await investigationPage.goto();
    await page.keyboard.press('Tab');
    await expect(investigationPage.questionInput).toBeFocused();
    await page.keyboard.type('Test question');
    await page.keyboard.press('Tab');
    await expect(investigationPage.submitButton).toBeFocused();
  });
});

test.describe('WebSocket Real-time Updates', () => {
  test('should receive real-time investigation updates', async ({ page }) => {
    const investigationPage = new InvestigationPage(page);
    await investigationPage.goto();

    const wsMessages: string[] = [];
    page.on('websocket', (ws) => {
      ws.on('framereceived', (frame) => {
        if (frame.payload) wsMessages.push(frame.payload.toString());
      });
    });

    await investigationPage.startInvestigation('Test WebSocket updates');
    await page.waitForTimeout(5000);
    expect(wsMessages.length).toBeGreaterThan(0);
  });
});

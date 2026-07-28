import { Page, Locator, expect } from '@playwright/test';
import { BasePage } from './base.page';

export class InvestigationPage extends BasePage {
  readonly questionInput: Locator;
  readonly submitButton: Locator;
  readonly investigationPanel: Locator;
  readonly approvalModal: Locator;
  readonly approveButton: Locator;
  readonly rejectButton: Locator;
  readonly toolCallsList: Locator;
  readonly hypothesisList: Locator;
  readonly rootCauseCard: Locator;
  readonly fixPrButton: Locator;

  constructor(page: Page) {
    super(page);
    this.questionInput = page.locator('[data-testid="question-input"]');
    this.submitButton = page.locator('[data-testid="submit-question"]');
    this.investigationPanel = page.locator('[data-testid="investigation-panel"]');
    this.approvalModal = page.locator('[data-testid="approval-modal"]');
    this.approveButton = page.locator('[data-testid="approve-action"]');
    this.rejectButton = page.locator('[data-testid="reject-action"]');
    this.toolCallsList = page.locator('[data-testid="tool-calls-list"]');
    this.hypothesisList = page.locator('[data-testid="hypothesis-list"]');
    this.rootCauseCard = page.locator('[data-testid="root-cause-card"]');
    this.fixPrButton = page.locator('[data-testid="fix-pr-button"]');
  }

  async goto() {
    await super.goto('/');
  }

  async startInvestigation(question: string) {
    await this.questionInput.fill(question);
    await this.submitButton.click();
    await expect(this.investigationPanel).toBeVisible({ timeout: 10000 });
  }

  async waitForToolCall(toolName: string, timeout: number = 30000) {
    await this.toolCallsList
      .locator(`[data-tool="${toolName}"]`)
      .waitFor({ state: 'visible', timeout });
  }

  async getToolCallCount(): Promise<number> {
    return await this.toolCallsList.locator('[data-testid="tool-call"]').count();
  }

  async waitForHypothesis(timeout: number = 60000) {
    await this.hypothesisList.locator('li').first().waitFor({ state: 'visible', timeout });
  }

  async waitForRootCause(timeout: number = 120000) {
    await this.rootCauseCard.waitFor({ state: 'visible', timeout });
  }

  async getRootCauseText(): Promise<string> {
    return await this.rootCauseCard.textContent() || '';
  }

  async approveAction() {
    await expect(this.approvalModal).toBeVisible();
    await this.approveButton.click();
    await expect(this.approvalModal).not.toBeVisible();
  }

  async rejectAction() {
    await expect(this.approvalModal).toBeVisible();
    await this.rejectButton.click();
    await expect(this.approvalModal).not.toBeVisible();
  }
}

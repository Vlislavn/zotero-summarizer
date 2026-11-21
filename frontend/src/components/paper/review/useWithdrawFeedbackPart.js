import { useMutation } from '@tanstack/react-query';
import { withdrawReviewFeedbackPart } from '../../../api/libraryApi.js';
import { PART_LABELS } from './reviewFeedbackFields.js';

// Withdraw the owner's feedback on one part of a review. `mutate(field)`; `onDone(data, receiptText)` gets the
// server's remaining feedback row and the «Withdrawn · …» receipt.
export default function useWithdrawFeedbackPart(itemKey, reviewedAt, onDone) {
  return useMutation({
    mutationFn: (field) => withdrawReviewFeedbackPart(itemKey, field, reviewedAt),
    onSuccess: (data, field) => onDone(data, `Withdrawn · ${PART_LABELS[field]}`),
  });
}

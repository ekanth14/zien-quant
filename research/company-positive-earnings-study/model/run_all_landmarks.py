"""Record real-data training attempts at all registered morning landmarks."""
import argparse
import json
from pathlib import Path
from positive_path_model import LANDMARKS,train

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,default=Path(__file__).with_name('local_model_dataset_with_market.json'))
    parser.add_argument('--output-dir',type=Path,default=Path(__file__).resolve().parent/'actual_training_attempts')
    args=parser.parse_args();args.output_dir.mkdir(parents=True,exist_ok=True)
    dataset=json.loads(args.dataset.read_text());summary=[]
    for landmark in LANDMARKS:
        artifact=train(dataset,landmark)
        output=args.output_dir/f"{landmark.replace(':','')}.json"
        output.write_text(json.dumps(artifact,indent=2,allow_nan=False)+'\n')
        audit=artifact['data_audit']
        summary.append({'landmark_ist':landmark,'status':artifact['status'],
          'raw_company_sessions':audit['raw_landmark_rows'],
          'raw_distinct_dates':audit['raw_landmark_distinct_dates'],
          'verified_rows':audit['eligible_verified_rows'],
          'predictions_enabled':artifact['predictions_enabled'],
          'model_fitted':artifact['model'] is not None,'market_accuracy':artifact['test_metrics'],
          'blockers':artifact['blockers'],'rejection_counts':audit['rejection_counts'],
          'artifact':str(output.relative_to(Path(__file__).resolve().parent))})
    destination=Path(__file__).with_name('actual_training_attempts_all_landmarks.json')
    destination.write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(json.dumps(summary,indent=2))

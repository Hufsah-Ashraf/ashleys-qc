#!/usr/bin/env python


import pandas as pd
import numpy as np
import pickle


def add_prediction_parser(subparsers):
    parser = subparsers.add_parser('predict', help='predict class probabilities for new cells')
    parser.add_argument('--path', '-p', help='path to feature table of data that should be predicted', required=True)
    parser.add_argument('--output', '-o', help='folder for output file', required=True)
    parser.add_argument('--model', '-m', help='pkl model to use for prediction', required=True)
    parser.add_argument('--annotation', '-a', help='path to folder with annotation files', required=False)
    parser.add_argument('--filter', dest='filter', action='store_true')
    parser.add_argument('--no-filter', dest='filter', action='store_false')
    parser.set_defaults(filter=False)

    parser.set_defaults(execute=run_prediction)

    return subparsers


def predict_model(model_name, output_name, features):
    with open(model_name, 'rb') as m:
        clf = pickle.load(m)
        prediction = clf.predict(features)
        probability = clf.predict_proba(features)[:, 1]
    # plot_hist(output_name, probability, None)
    return prediction, probability


def evaluate_prediction(probability, annotation, dataset):
    names = dataset['sample_name'].values
    class_list = []
    with open(annotation) as f:
        annotation_list = [line.rstrip() for line in f]

    for n in names:
        if n in annotation_list:
            class_list.append(1)
        else:
            class_list.append(0)

    tp = 0
    fp = 0
    tn = 0
    fn = 0
    for p, c in zip(probability, class_list):
        if c == 1:
            if p < 0.5:
                fn += 1
            else:
                tp += 1
        else:
            if p < 0.5:
                tn += 1
            else:
                fp += 1

    print('accuracy: ' + str((tp + tn)/(tp+tn+fp+fn)))
    print('tp: ' + str(tp) + ', tn: ' + str(tn) + ', fp: ' + str(fp) + ', fn: ' + str(fn))
    return class_list


def filter_low_read_counts(dataset):
    cut = 23000
    filtered = dataset.loc[dataset['total_0.2mb'] < cut]
    index_names = dataset[dataset['total_0.2mb'] < cut].index
    dataset.drop(index_names, inplace=True)
    filtered_names = filtered['sample_name'].values
    return filtered_names


def run_prediction(args):
    model = args.model
    path = args.path
    output = args.output
    annotation = args.annotation
    filter_cells = args.filter

    dataset = pd.read_csv(path, sep='\s+')
    filtered_cells = filter_low_read_counts(dataset)
    features = dataset.drop(columns=['sample_name'])
    names = dataset['sample_name'].values

    # load model
    prediction, probability = predict_model(model, output + 'prediction', features)

    if filter_cells:
        names = np.concatenate((names, filtered_cells))
        print(names)
        prediction_filtered = [0] * len(filtered_cells)
        prediction = np.concatenate((prediction, prediction_filtered))
        probability = np.concatenate((probability, prediction_filtered))

    if annotation is not None:
        classes = evaluate_prediction(probability, annotation, dataset)
        # plot_hist(output + 'prediction_annotation', probability, classes)

    file = open(output + 'prediction_probabilities.tsv', 'w')
    critical = open(output + 'critical_predictions.tsv', 'w')
    file.write('cell\tprediction\tprobability\n')
    critical.write('cell\tprobability\n')
    for i in range(len(names)):
        file.write(names[i] + '\t' + str(prediction[i]) + '\t' + str(round(probability[i], 4)) + '\n')
        if 0.3 < probability[i] < 0.7:
            critical.write(names[i] + '\t' + str(round(probability[i], 4)) + '\n')

    file.close()

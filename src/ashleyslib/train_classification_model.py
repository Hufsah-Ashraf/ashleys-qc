

import statistics
from sklearn import svm
from sklearn import ensemble
import pandas as pd
from sklearn.model_selection import GridSearchCV
import numpy as np
import json
from time import time
import pickle


best_params = dict()
best_result = 0
cv_runs = 5
n_jobs = 20
feature_imp = []
roc_count = 0
total_accuracy = 0


def add_training_parser(subparsers):
    parser = subparsers.add_parser('train', help='train new classification model')
    parser.add_argument('--iterations', '-i', default=50, required=False, type=int,
                        help="number of times new datasets are generated for model creation, initial: 50")
    parser.add_argument('--path', '-p', help='path to feature table', required=True)
    parser.add_argument('--annotation', '-a', help='path to annotation file containing class 1 cells', required=True)
    parser.add_argument('--test', '-t', help='path to test dataset, if not given, -p is split in test and train dataset', required=False)
    parser.add_argument('--features', '-f', help='number of features used from feature table, using all if not specified',
                        required=False, type=int)
    parser.add_argument('--output', '-o', help='name of output file', required=True)
    parser.add_argument('--cv_runs', '-c', help='number of cv runs performed by grid search, initial: 5',
                        required=False, default=5, type=int)
    parser.add_argument('--n_jobs', '-n', help='number of jobs for grid search', required=False, default=20, type=int)
    parser.add_argument('--json', '-j', help='json file with the parameters for grid search', required=True)

    model_parser = parser.add_mutually_exclusive_group(required=False)
    model_parser.add_argument('--svc', dest='classifier', action='store_true', help='runs support vector classification')
    model_parser.add_argument('--gb', dest='classifier', action='store_false', help='runs gradient boosting')
    parser.set_defaults(classifier=True)
    parser.set_defaults(execute=run_model_training)

    return subparsers


# evaluate model performance by comparing expected output and true output
def evaluation(prediction, true_values, test, prediction_dataset, current_iteration):
    tp = 0
    tn = 0
    fp = 0
    fn = 0
    counter = 0
    wrong_ids = []
    correct_ids = []
    rows, cols = test.shape
    names = test['sample_name'].values

    # create dataframe with overall prediction results:
    # if cell was part of test dataset: value equals prediction probability (for class 1)
    # if cell was part of training dataset: value is -1
    rows, cols = prediction_dataset.shape
    insert_column = [-1] * rows
    all_names = prediction_dataset['name'].values
    for n, i in zip(all_names, range(rows)):
        if n in names:
            pred = prediction[list(names).index(n)]
            insert_column[i] = round(pred, 4)
    prediction_dataset['i' + str(current_iteration)] = insert_column

    zero_one_prediction = []
    for p in prediction:
        if p < 0.5:
            zero_one_prediction.append(0)
        else:
            zero_one_prediction.append(1)

    for p, t, s in zip(zero_one_prediction, true_values, names):
        counter += 1
        if p == 0 and t == 0:
            tn += 1
            correct_ids.append(s)
        elif p == 0 and t == 1:
            fn += 1
            wrong_ids.append(s)
        elif p == 1 and t == 1:
            tp += 1
            correct_ids.append(s)
        elif p == 1 and t == 0:
            fp += 1
            wrong_ids.append(s)

    sensitivity = tp / (tp + fn)
    specificity = tn / (tn + fp)
    precision = tp / (tp + fp)
    accuracy = (tp + tn) / counter

    return sensitivity, specificity, precision, accuracy, fp, fn, tp, tn, wrong_ids, correct_ids


# add first column to dataset, based on annotation file: all cells contained in file are labeled 1
# TODO: include case for GM vs NA???
def add_class_column(dataset, annotation):
    prediction_dataset = pd.DataFrame()
    names = dataset['sample_name'].values
    class_list = []

    for n in names:
        if n in annotation:
            class_list.append(1)
        else:
            class_list.append(0)

    dataset.insert(loc=0, column='class', value=class_list, allow_duplicates=True)
    prediction_dataset['name'] = names
    prediction_dataset['class'] = class_list
    return dataset, prediction_dataset


# exclude all cells with a total count lower than 'cut' from the classification
def filter_low_read_counts(dataset):
    total_windows = dataset['total_0.2mb'].values
    mean = statistics.mean(total_windows)
    stdev = statistics.stdev(total_windows)
    cut = 23000  # mean - stdev
    print(cut)
    index_names = dataset[dataset['total_0.2mb'] < cut].index
    dataset.drop(index_names, inplace=True)
    return dataset


# split dataset into train and test data, with equal class size for training
def train_test_split(dataset, test_flag):
    y = dataset['class'].values
    size = len(dataset.index)
    df_0 = pd.DataFrame(columns=list(dataset.columns))
    df_1 = pd.DataFrame(columns=list(dataset.columns))

    for s in range(size):
        if y[s] == 0:
            df_0 = df_0.append(dataset[s:s+1])
        if y[s] == 1:
            df_1 = df_1.append(dataset[s:s+1])

    smaller_size = min(len(df_0.index), len(df_1.index))
    if test_flag:
        zeros = np.split(df_0, [int(smaller_size+1)])
        ones = np.split(df_1, [int(smaller_size+1)])

        train = pd.concat([zeros[0], ones[0]])
        test = []

    else:
        zeros = np.split(df_0, [int(((smaller_size+1)/4)*3)])
        ones = np.split(df_1, [int(((smaller_size+1)/4)*3)])

        train = pd.concat([zeros[0], ones[0]])
        test = pd.concat([zeros[1], ones[1]])
        test = test.sample(frac=1, random_state=2)

    train = train.sample(frac=1, random_state=2)

    return test, train


# write feature importance of current model to output file
def feature_importance(imp_file, iteration, imp_values):
    imp_file.write(str(iteration))
    for i in imp_values:
        imp_file.write('\t' + str(i))
    imp_file.write('\n')


# create model (gradient boosting or support vector)
def create_model(test, train, model, features, file, parameters, log_feature_imp, n, log_all_models, prediction_dataset, current_iteration):
    global best_result, best_params, total_accuracy

    y_train = train['class'].values
    y_train = y_train.astype('int')
    y_test = test['class'].values

    x_train = train.iloc[:, 1:features+1].values
    x_test = test.iloc[:, 1:features+1].values
    samples_test = test.ix[:, 'sample_name'].values

    if model == 'gb':
        clf = create_gb(x_train, y_train, parameters)
        feature_importance(log_feature_imp, n, feature_imp)
    else:
        clf = create_svc(x_train, y_train, parameters)

    prediction = clf.predict_proba(x_test)[:, 1]  # prediction probabilities
    # prediction = clf.predict(x_test)  # predicted class
    params = clf.best_params_

    sensitivity, specificity, precision, accuracy, fp, fn, tp, tn, wrong, correct = evaluation(prediction, y_test, test, prediction_dataset, current_iteration)
    total_accuracy += accuracy
    log_all_models.write(str(round(accuracy, 4)) + '\t' + str(round(sensitivity, 4)) + '\t' + str(round(specificity, 4))
                         + '\t' + str(wrong) + '\n')
    # file.write('prediction_[] = ' + str(prediction) + '\ny_test_[] = ' + str(y_test) + '\n')

    if best_result < accuracy:
        best_result = accuracy
        best_params = params
        # file.write('prediction:\n' + str(prediction) + '\ny_test:\n' + str(y_test))
        file.write('\nnew best parameter combination: ' + str(best_params) + ' with accuracy: ' + str(best_result))
        file.write('\nsensitivity: {}, specificity: {}, precision: {}\n'.format(sensitivity, specificity, precision))
        file.write('false positives: ' + str(fp) + ' false negatives: ' + str(fn) + ' true positives: ' + str(tp)
                   + ' true negatives: ' + str(tn) + '\n\n')
        if model == 'gb':
            file.write('feature importance: {}\n\n'.format(feature_imp))

    return wrong, samples_test, correct


# performing grid search with support vector classifier based on specified parameters
def create_svc(x_train, y_train, parameters):
    # note: usage of linear kernel may lead to divergence
    svc = svm.SVC(random_state=2, kernel='rbf')
    clf = GridSearchCV(svc, parameters, cv=cv_runs, n_jobs=n_jobs)

    clf.fit(x_train, y_train)

    return clf


# performing grid search with gradient boosting classifier based on specified parameters in json file
def create_gb(x_train, y_train, parameters):
    global feature_imp

    gbc = ensemble.GradientBoostingClassifier(random_state=2)
    clf = GridSearchCV(gbc, parameters, cv=cv_runs, n_jobs=n_jobs)
    clf.fit(x_train, y_train)

    feature_imp = clf.best_estimator_.feature_importances_

    return clf


# create an output file listing all samples that were wrongly predicted
def outfile_wrong_predictions(wrong, correct, samples, file, file_correct):
    dict_wrong = dict()
    dict_correct = dict()
    samples_tested = dict()
    for list in wrong:
        for w in list:
            if w in dict_wrong:
                dict_wrong[w] = dict_wrong[w] + 1
            else:
                dict_wrong[w] = 1

    for s_list in samples:
        for s in s_list:
            if s in samples_tested:
                samples_tested[s] = samples_tested[s] + 1
            else:
                samples_tested[s] = 1

    for c_list in correct:
        for c in c_list:
            if c in dict_wrong:
                continue
            elif c in dict_correct:
                dict_correct[c] = dict_correct[c] + 1
            else:
                dict_correct[c] = 1

    file.write('samples\twrong_predictions\ttested\tpercentage\n')
    file_correct.write('samples\tpredictions\n')

    for key, value in sorted(dict_wrong.items()):
        file.write(str(key) + '\t' + str(value) + '\t' + str(samples_tested[key]) + '\t' +
                   str(round(value/samples_tested[key] * 100, 2)) + '\n')
    for key, value in sorted(dict_correct.items()):
        file_correct.write(str(key) + '\t' + str(value) + '\n')

    return


def run_model_training(args):
    start_time = time()
    num = args.iterations
    path = args.path
    features = args.features
    svc_model = args.classifier
    output = args.output
    n_jobs = args.n_jobs
    cv_runs = args.cv_runs
    params_file = args.json
    test_data = args.test
    annotation_file = args.annotation

    test_flag = False
    roc_count = 0

    with open(annotation_file) as f:
        annotation = [line.rstrip() for line in f]

    # in case extra file with dataset for testing is given
    if test_data is not None:
        test_flag = True
        test_data = pd.read_csv(test_data, sep='\s+', header=None)

    with open(params_file, 'r') as jfile:
        params = json.load(jfile)

    dataset = pd.read_csv(path, sep='\s+', header=0)
    feature_names = dataset.columns
    dataset, prediction_dataset = add_class_column(dataset, annotation)
    # dataset = filter_low_read_counts(dataset)

    print(dataset.shape)
    file = open(output, 'w')
    log_name = output.split('.tsv')
    file_correct = open(log_name[0] + '_correct.tsv', 'w')

    if features is None:
        features = dataset.shape[1] - 2
        print(features)

    log_file = open(log_name[0] + '_log.tsv', 'w')
    log_all_models = open(log_name[0] + '_model_log.tsv', 'w')
    log_feature_imp = open(log_name[0] + '_feature_imp.tsv', 'w')
    wrong_predictions = []
    correct_predictions = []
    samples_tested = []

    log_file.write('Input: ' + str(path) + '\n')
    log_file.write('used parameters: ' + str(params) + '\n')
    log_all_models.write('accuracy\tsensitivity\tspecificity\twrong_predicted\n')
    feature_importance(log_feature_imp, 'iteration', feature_names[:-1])

    if svc_model:
        log_file.write('running ' + str(num) + ' iterations creating support vector classifiers: \n\n')
    else:
        log_file.write('running ' + str(num) + ' iterations creating gradient boosting classifiers: \n\n')

    for n in range(num):
        print('current iteration: ' + str(n))
        current_iteration = n
        dataset = dataset.sample(frac=1, random_state=2)

        test, train = train_test_split(dataset, test_flag)
        if test_flag:
            test = test_data

        if svc_model:
            wrong, samples, correct = create_model(test, train, 'svc', features, log_file, params, log_feature_imp, n,
                                                   log_all_models, prediction_dataset, current_iteration)
        else:
            wrong, samples, correct = create_model(test, train, 'gb', features, log_file, params, log_feature_imp, n,
                                                   log_all_models, prediction_dataset, current_iteration)
        wrong_predictions.append(wrong)
        correct_predictions.append(correct)
        samples_tested.append(samples)

    y = dataset['class'].values
    x = dataset.iloc[:, 1:features+1].values
    final_clf = create_gb(x, y, params)
    log_file.write('Final model\nfeature importance: {}\n\n'.format(feature_imp))

    # save final model
    with open(log_name[0] + '.pkl', 'wb') as f:
        pickle.dump(final_clf, f)

    prediction_dataset.to_csv(log_name[0] + '_prediction.tsv', sep='\t', index=False)

    outfile_wrong_predictions(wrong_predictions, correct_predictions, samples_tested, file, file_correct)
    log_file.write('\nmean accuracy: ' + str(total_accuracy/num))

    end_time = time()
    log_file.write('\ntime needed for model creation and prediction: ' + str(end_time - start_time))

    file.close()
    file_correct.close()
    log_file.close()
    log_all_models.close()
